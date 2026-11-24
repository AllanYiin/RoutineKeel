"""Source contents, previews and project pointers commit in one SQLite transaction."""

import hashlib
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from ..contracts import StudioError
from ..project.models import canonical_json, validate_name
from ..project.service import ProjectService, now
from .folder import collect_path
from .models import ImportLimits, Source
from .preview import preview_file
from .security import safe_relative


class SourceService:
    def __init__(self, repository, limits: ImportLimits | None = None):
        self.repository = repository
        self.projects = ProjectService(repository)
        self.limits = limits or ImportLimits()

    def import_text(
        self, project_id: str, expected_version: int, text: str, *, name="說明.txt", source_id=None
    ):
        if not isinstance(text, str):
            raise StudioError("INPUT_INVALID", "來源必須是文字。")
        return self._save(
            project_id, expected_version, {name: text.encode("utf-8")}, name, source_id
        )

    def import_path(
        self, project_id: str, expected_version: int, path: str | Path, *, source_id=None
    ):
        path = Path(path)
        files = collect_path(path, self.limits)
        metadata = {}
        if path.is_dir():
            metadata = {name: (path / name).lstat().st_mtime_ns for name in files}
        elif path.suffix.lower() != ".zip":
            metadata = {path.name: path.lstat().st_mtime_ns}
        return self._save(
            project_id, expected_version, files, path.name, source_id, metadata, allow_empty=True
        )

    def _save(
        self,
        project_id,
        expected_version,
        files,
        name,
        source_id,
        metadata=None,
        *,
        previews=None,
        extra_metadata=None,
        source_kind=None,
        allow_empty=False,
    ):
        name = validate_name(name)
        records, blobs = [], {}
        total = 0
        for relative, payload in sorted(files.items()):
            safe_relative(relative)
            total += len(payload)
            if len(payload) > self.limits.max_file_bytes or total > self.limits.max_total_bytes:
                raise StudioError("INPUT_INVALID", "來源大小超過限制。")
            preview = (previews or {}).get(relative) or preview_file(
                relative, payload, self.limits, allow_empty=allow_empty
            )
            digest = hashlib.sha256(payload).hexdigest()
            blobs[digest] = payload
            records.append(
                {
                    "path": relative,
                    "sha256": digest,
                    "size": len(payload),
                    "preview": asdict(preview),
                    "observed_mtime_ns": (metadata or {}).get(relative),
                }
            )
        if not records or len(records) > self.limits.max_files:
            raise StudioError("INPUT_INVALID", "來源檔案數量不符合限制。")
        kind = source_kind or (records[0]["preview"]["kind"] if len(records) == 1 else "folder")
        manifest = canonical_json({"files": records, "description": "", **(extra_metadata or {})})
        content_hash = hashlib.sha256(
            canonical_json(
                {"files": [{"path": r["path"], "sha256": r["sha256"]} for r in records]}
            ).encode()
        ).hexdigest()
        with self.repository.transaction() as db:
            self.projects._editable(db, project_id, expected_version)
            if source_id is None:
                # Same snapshot is idempotent, including when a folder is imported twice.
                existing = db.execute(
                    """SELECT s.source_id FROM studio_sources s JOIN studio_source_versions v
                    ON s.source_id=v.source_id AND s.current_version=v.version
                    WHERE s.project_id=? AND s.status='active' AND v.content_hash=?""",
                    (project_id, content_hash),
                ).fetchone()
                if existing:
                    return self.get(project_id, existing[0])
                source_id, version = "source_" + uuid4().hex, 1
                db.execute(
                    "INSERT INTO studio_sources VALUES (?,?,1,'active')", (source_id, project_id)
                )
            else:
                current = self.get(project_id, source_id)
                if current.status != "active":
                    raise StudioError("SOURCE_REMOVED", "請新增來源，不能覆寫已移除的來源。")
                version = current.version + 1
                db.execute(
                    "UPDATE studio_sources SET current_version=? WHERE source_id=?",
                    (version, source_id),
                )
            for digest, content in blobs.items():
                db.execute(
                    "INSERT OR IGNORE INTO studio_source_blobs VALUES (?,?)", (digest, content)
                )
            db.execute(
                "INSERT INTO studio_source_versions VALUES (?,?,?,?,?,?,?)",
                (source_id, version, name, kind, content_hash, manifest, now()),
            )
            self._touch(db, project_id)
        return self.get(project_id, source_id)

    @staticmethod
    def _touch(db, project_id):
        db.execute(
            "UPDATE studio_projects SET version=version+1,updated_at=? WHERE project_id=?",
            (now(), project_id),
        )

    def get(self, project_id: str, source_id: str, *, version: int | None = None) -> Source:
        row = self.repository.connection.execute(
            """SELECT s.source_id,s.project_id,v.version,v.name,v.kind,v.content_hash,
            v.manifest_json,s.status,v.created_at FROM studio_sources s
            JOIN studio_source_versions v ON s.source_id=v.source_id
            AND v.version=COALESCE(?,s.current_version) WHERE s.project_id=? AND s.source_id=?""",
            (version, project_id, source_id),
        ).fetchone()
        if row is None:
            raise StudioError("SOURCE_NOT_FOUND", "找不到此專案的來源版本。")
        return Source(**dict(row))

    def list(self, project_id: str, *, include_removed=False) -> list[Source]:
        self.projects.get(project_id)
        return [
            self.get(project_id, row[0])
            for row in self.repository.connection.execute(
                "SELECT source_id FROM studio_sources WHERE project_id=? AND (status='active' OR ?) ORDER BY rowid",
                (project_id, include_removed),
            )
        ]

    def describe(
        self, project_id: str, source_id: str, expected_version: int, description: str
    ) -> Source:
        if not isinstance(description, str) or len(description) > 8000:
            raise StudioError("INPUT_INVALID", "來源說明須為最多 8000 字的文字。")
        with self.repository.transaction() as db:
            self.projects._editable(db, project_id, expected_version)
            source = self.get(project_id, source_id)
            if source.status != "active":
                raise StudioError("SOURCE_REMOVED", "來源已移除。")
            manifest = source.manifest
            manifest["description"] = description
            db.execute(
                "INSERT INTO studio_source_versions VALUES (?,?,?,?,?,?,?)",
                (
                    source_id,
                    source.version + 1,
                    source.name,
                    source.kind,
                    source.content_hash,
                    canonical_json(manifest),
                    now(),
                ),
            )
            db.execute(
                "UPDATE studio_sources SET current_version=current_version+1 WHERE source_id=?",
                (source_id,),
            )
            self._touch(db, project_id)
        return self.get(project_id, source_id)

    def remove(self, project_id: str, source_id: str, expected_version: int):
        with self.repository.transaction() as db:
            self.projects._editable(db, project_id, expected_version)
            source = self.get(project_id, source_id)
            if source.status == "removed":
                return source
            db.execute("UPDATE studio_sources SET status='removed' WHERE source_id=?", (source_id,))
            self._touch(db, project_id)
        return self.get(project_id, source_id)

    def content(self, project_id: str, source_id: str, relative: str, *, version=None) -> bytes:
        source = self.get(project_id, source_id, version=version)
        record = next((r for r in source.manifest["files"] if r["path"] == relative), None)
        if record is None:
            raise StudioError("SOURCE_NOT_FOUND", "來源沒有此檔案。")
        row = self.repository.connection.execute(
            "SELECT content FROM studio_source_blobs WHERE sha256=?", (record["sha256"],)
        ).fetchone()
        if row is None or hashlib.sha256(row[0]).hexdigest() != record["sha256"]:
            raise StudioError("SOURCE_CORRUPT", "來源副本指紋不符，請重新匯入。")
        return row[0]
