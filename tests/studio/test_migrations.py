import hashlib
import json
import sqlite3
import subprocess
import sys

import pytest
from workflow_studio.contracts import StudioError
from workflow_studio.service import StudioService
from workflow_studio.storage.migrations import SCHEMA_VERSION
from workflow_studio.storage.sqlite import SQLiteRepository


def test_migration_idempotent_backup_and_restore(tmp_path):
    with StudioService.open_workspace(tmp_path / "workspace") as studio:
        p = studio.projects.create("需要還原", {"count": 3})
        snapshot = studio.repository.backup()
        metadata = json.loads(snapshot.with_suffix(".json").read_text())
        assert metadata["sha256"] == hashlib.sha256(snapshot.read_bytes()).hexdigest()
        assert metadata["schema_version"] == SCHEMA_VERSION
        backup_count = len(list(snapshot.parent.glob("*.sqlite3")))
    with StudioService.open_workspace(tmp_path / "workspace") as studio:
        assert studio.projects.get(p.project_id) == p
        assert len(list(snapshot.parent.glob("*.sqlite3"))) == backup_count
    root = tmp_path / "restored/share_states/workflow_studio"
    SQLiteRepository.restore(snapshot, root)
    with StudioService.open_workspace(tmp_path / "restored") as studio:
        assert studio.projects.get(p.project_id) == p
    with pytest.raises(FileExistsError):
        SQLiteRepository.restore(snapshot, root)


def test_corrupt_backup_rejected_without_target(tmp_path):
    repo = SQLiteRepository(tmp_path / "source")
    snapshot = repo.backup()
    repo.close()
    snapshot.write_bytes(snapshot.read_bytes() + b"corruption")
    with pytest.raises(StudioError) as caught:
        SQLiteRepository.restore(snapshot, tmp_path / "destination")
    assert caught.value.code == "BACKUP_INVALID"
    assert not (tmp_path / "destination/studio.sqlite3").exists()


def test_newer_schema_rejected_without_mutation(tmp_path):
    repo = SQLiteRepository(tmp_path)
    repo.connection.execute("PRAGMA user_version=999")
    repo.close()
    before = (tmp_path / "studio.sqlite3").read_bytes()
    with pytest.raises(StudioError) as caught:
        SQLiteRepository(tmp_path)
    assert caught.value.code == "SCHEMA_TOO_NEW"
    assert (tmp_path / "studio.sqlite3").read_bytes() == before


def test_foreign_database_rejected(tmp_path):
    db = sqlite3.connect(tmp_path / "studio.sqlite3")
    db.execute("CREATE TABLE runs(id TEXT)")
    db.commit()
    db.close()
    before = (tmp_path / "studio.sqlite3").read_bytes()
    with pytest.raises(StudioError) as caught:
        SQLiteRepository(tmp_path)
    assert caught.value.code == "STORAGE_INVALID"
    assert (tmp_path / "studio.sqlite3").read_bytes() == before


def test_failed_migration_rolls_back_and_preserves_backup(tmp_path, monkeypatch):
    from workflow_studio.storage import sqlite as storage

    monkeypatch.setattr(storage, "MIGRATIONS", {1: ("CREATE TABLE draft(id TEXT)", "INVALID SQL")})
    with pytest.raises(sqlite3.OperationalError):
        SQLiteRepository(tmp_path)
    db = sqlite3.connect(tmp_path / "studio.sqlite3")
    assert db.execute("PRAGMA user_version").fetchone()[0] == 0
    assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == []
    db.close()
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 1


def test_backup_failure_prevents_migration(tmp_path, monkeypatch):
    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(SQLiteRepository, "backup", fail)
    with pytest.raises(OSError, match="disk full"):
        SQLiteRepository(tmp_path)
    db = sqlite3.connect(tmp_path / "studio.sqlite3")
    assert db.execute("PRAGMA user_version").fetchone()[0] == 0
    db.close()


def test_missing_table_rejected(tmp_path):
    repo = SQLiteRepository(tmp_path)
    repo.connection.execute("DROP TABLE studio_revisions")
    repo.close()
    with pytest.raises(StudioError, match="資料表不完整"):
        SQLiteRepository(tmp_path)


def test_correct_hash_but_invalid_sqlite_rejected(tmp_path):
    snapshot = tmp_path / "invalid.sqlite3"
    snapshot.write_bytes(b"not a database")
    snapshot.with_suffix(".json").write_text(
        json.dumps(
            {"schema_version": 1, "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest()}
        )
    )
    with pytest.raises(StudioError) as caught:
        SQLiteRepository.restore(snapshot, tmp_path / "destination")
    assert caught.value.code == "BACKUP_INVALID"
    assert not (tmp_path / "destination/studio.sqlite3").exists()


def test_process_death_before_commit_keeps_previous_revision(tmp_path):
    with StudioService.open_workspace(tmp_path) as studio:
        p = studio.projects.create("中斷")
        dbpath = studio.repository.path
    script = """
import os, sqlite3, sys
db=sqlite3.connect(sys.argv[1], isolation_level=None)
db.execute('BEGIN IMMEDIATE')
db.execute("UPDATE studio_projects SET name='not committed'")
os._exit(9)
"""
    result = subprocess.run([sys.executable, "-c", script, str(dbpath)])
    assert result.returncode == 9
    with StudioService.open_workspace(tmp_path) as studio:
        assert studio.projects.get(p.project_id) == p
        assert studio.repository.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
