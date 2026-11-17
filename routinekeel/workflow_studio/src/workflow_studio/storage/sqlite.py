"""Dedicated transactional database with verified, restorable snapshots."""

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from ..contracts import StudioError
from .migrations import APPLICATION_ID, MIGRATIONS, SCHEMA_VERSION


class SQLiteRepository:
    def __init__(self, root: Path | str):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "studio.sqlite3"
        if self.path.is_symlink():
            raise StudioError("INPUT_INVALID", "Studio 資料庫不能是符號連結。")
        self.connection = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA synchronous=FULL")
        try:
            self._migrate()
        except BaseException:
            self.close()
            raise

    @property
    def schema_version(self) -> int:
        return self.connection.execute("PRAGMA user_version").fetchone()[0]

    def _validate_identity(self):
        identity = self.connection.execute("PRAGMA application_id").fetchone()[0]
        tables = self.connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if identity != APPLICATION_ID and (identity != 0 or tables or self.schema_version):
            raise StudioError("STORAGE_INVALID", "這不是 Studio 專用資料庫。")
        if self.schema_version > SCHEMA_VERSION:
            raise StudioError("SCHEMA_TOO_NEW", "資料由較新版本建立，請使用相容版本開啟。")

    def _migrate(self):
        with self.transaction():
            self._validate_identity()
            version = self.schema_version
            if version < SCHEMA_VERSION:
                # A separate read connection can snapshot while we hold the migration write lock.
                self.backup()
                for target in range(version + 1, SCHEMA_VERSION + 1):
                    for statement in MIGRATIONS[target]:
                        self.connection.execute(statement)
                    self.connection.execute(f"PRAGMA user_version={target}")
                self.connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            required = {"studio_projects", "studio_revisions"}
            if SCHEMA_VERSION >= 2:
                required |= {"studio_sources", "studio_source_versions", "studio_source_blobs"}
            if SCHEMA_VERSION >= 3:
                required |= {"studio_tools", "studio_tool_approvals"}
            if SCHEMA_VERSION >= 4:
                required |= {"studio_events", "studio_llm_calls"}
            if SCHEMA_VERSION >= 5:
                required |= {"studio_evidence_graphs", "studio_test_cases"}
            if SCHEMA_VERSION >= 6:
                required |= {"studio_builds"}
            if SCHEMA_VERSION >= 7:
                required |= {"studio_verifications"}
            if SCHEMA_VERSION >= 8:
                required |= {"studio_approvals", "studio_releases"}
            if SCHEMA_VERSION >= 9:
                required |= {"studio_project_settings"}
            if SCHEMA_VERSION >= 10:
                required |= {"studio_evidence_decisions"}
            if SCHEMA_VERSION >= 11:
                required |= {"studio_tool_events"}
            if SCHEMA_VERSION >= 12:
                required |= {"studio_handler_templates"}
            if SCHEMA_VERSION >= 13:
                required |= {"studio_build_reviews"}
            actual = {
                row[0]
                for row in self.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not required <= actual:
                raise StudioError("STORAGE_INVALID", "Studio 資料表不完整，請由備份還原。")

    @contextmanager
    def transaction(self):
        if self.connection.in_transaction:
            savepoint = "nested_" + uuid4().hex
            self.connection.execute(f"SAVEPOINT {savepoint}")
            try:
                yield self.connection
                self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            except BaseException:
                self.connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                self.connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            return
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield self.connection
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def backup(self) -> Path:
        folder = self.root / "backups"
        if folder.is_symlink():
            raise StudioError("INPUT_INVALID", "備份資料夾不能是符號連結。")
        folder.mkdir(exist_ok=True)
        snapshot = folder / f"before-v{self.schema_version:03d}-{uuid4().hex}.sqlite3"
        source = sqlite3.connect(self.path)
        destination = sqlite3.connect(snapshot)
        try:
            source.backup(destination)
            if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise StudioError("BACKUP_INVALID", "資料備份完整性檢查失敗。")
            version = destination.execute("PRAGMA user_version").fetchone()[0]
        finally:
            destination.close()
            source.close()
        digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
        snapshot.with_suffix(".json").write_text(
            json.dumps({"schema_version": version, "sha256": digest}), encoding="utf-8"
        )
        return snapshot

    @staticmethod
    def restore(snapshot: Path | str, destination_root: Path | str) -> Path:
        """Restore only into a new location; never overwrite a running or existing DB."""
        snapshot = Path(snapshot)
        metadata = json.loads(snapshot.with_suffix(".json").read_text(encoding="utf-8"))
        payload = snapshot.read_bytes()
        if hashlib.sha256(payload).hexdigest() != metadata["sha256"]:
            raise StudioError("BACKUP_INVALID", "備份指紋不符，未還原。")
        validation = sqlite3.connect(":memory:")
        try:
            validation.deserialize(payload)
            valid = validation.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            version = validation.execute("PRAGMA user_version").fetchone()[0]
            identity = validation.execute("PRAGMA application_id").fetchone()[0]
            if (
                not valid
                or version != metadata["schema_version"]
                or identity not in {0, APPLICATION_ID}
                or (identity == 0 and version != 0)
            ):
                raise StudioError("BACKUP_INVALID", "備份結構或版本檢查失敗。")
        except sqlite3.DatabaseError as exc:
            raise StudioError("BACKUP_INVALID", "備份不是完整的 SQLite 資料庫。") from exc
        finally:
            validation.close()
        root = Path(destination_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        target = root / "studio.sqlite3"
        # Exclusive creation also rejects existing symlinks and avoids clobbering.
        with target.open("xb") as stream:
            try:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            except BaseException:
                stream.close()
                target.unlink()
                raise
        return target

    def close(self) -> None:
        self.connection.close()
