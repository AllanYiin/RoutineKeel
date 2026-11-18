import sqlite3

import pytest

pytest.importorskip("PIL", reason="Run source ingestion tests in the declared Studio environment")
from workflow_studio.contracts import StudioError
from workflow_studio.service import StudioService


def test_source_lifecycle_snapshots_and_reopen(tmp_path):
    original = tmp_path / "原始 文件.txt"
    original.write_text("第一版", encoding="utf-8")
    with StudioService.open_workspace(tmp_path / "workspace") as studio:
        p = studio.projects.create("來源")
        source = studio.sources.import_path(p.project_id, 1, original)
        assert source.manifest["files"][0]["preview"]["text"] == "第一版"
        original.write_text("第二版", encoding="utf-8")
        new = studio.sources.import_path(p.project_id, 2, original, source_id=source.source_id)
        assert new.version == 2
        assert (
            studio.sources.content(
                p.project_id, source.source_id, original.name, version=1
            ).decode()
            == "第一版"
        )
        described = studio.sources.describe(p.project_id, source.source_id, 3, "成功案例")
        assert described.manifest["description"] == "成功案例"
        studio.sources.remove(p.project_id, source.source_id, 4)
        assert studio.sources.list(p.project_id) == []
    with StudioService.open_workspace(tmp_path / "workspace") as studio:
        assert studio.sources.list(p.project_id, include_removed=True)[0].status == "removed"
        assert (
            studio.sources.content(
                p.project_id, source.source_id, original.name, version=1
            ).decode()
            == "第一版"
        )
    assert original.read_text(encoding="utf-8") == "第二版"


def test_duplicate_source_is_idempotent_and_blobs_deduplicated(tmp_path):
    with StudioService.open_workspace(tmp_path) as studio:
        p = studio.projects.create("去重")
        one = studio.sources.import_text(p.project_id, 1, "same")
        two = studio.sources.import_text(p.project_id, 2, "same")
        assert one == two
        assert studio.projects.get(p.project_id).version == 2
        studio.sources.import_text(p.project_id, 2, "same", name="另一個.txt")
        assert (
            studio.repository.connection.execute(
                "SELECT count(*) FROM studio_source_blobs"
            ).fetchone()[0]
            == 1
        )


def test_folder_preview_and_hundred_files_without_provider(tmp_path):
    folder = tmp_path / "成功 案例"
    (folder / "子目錄").mkdir(parents=True)
    for i in range(100):
        (folder / "子目錄" / f"{i}.txt").write_text(f"內容 {i}", encoding="utf-8")
    with StudioService.open_workspace(tmp_path / "workspace") as studio:
        p = studio.projects.create("資料夾")
        source = studio.sources.import_path(p.project_id, 1, folder)
        assert source.kind == "folder"
        assert len(source.manifest["files"]) == 100
        assert all(
            r["path"].startswith("子目錄/") and r["preview"]["text"]
            for r in source.manifest["files"]
        )


def test_failed_import_is_atomic_and_cannot_cross_project(tmp_path):
    with StudioService.open_workspace(tmp_path) as studio:
        p = studio.projects.create("甲")
        other = studio.projects.create("乙")
        source = studio.sources.import_text(p.project_id, 1, "private")
        with pytest.raises(StudioError) as caught:
            studio.sources.content(other.project_id, source.source_id, "說明.txt")
        assert caught.value.code == "SOURCE_NOT_FOUND"
        with pytest.raises(StudioError):
            studio.sources.import_text(p.project_id, 1, "stale")
        assert len(studio.sources.list(p.project_id)) == 1
        studio.repository.connection.execute("""CREATE TRIGGER fail_import BEFORE UPDATE ON studio_projects
            BEGIN SELECT RAISE(ABORT, 'injected failure'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            studio.sources.import_text(p.project_id, 2, "will rollback")
        assert len(studio.sources.list(p.project_id)) == 1
        assert (
            studio.repository.connection.execute(
                "SELECT count(*) FROM studio_source_blobs"
            ).fetchone()[0]
            == 1
        )


def test_v1_migration_keeps_projects_and_restorable_old_snapshot(tmp_path):
    from workflow_studio.storage.migrations import APPLICATION_ID, V001
    from workflow_studio.storage.sqlite import SQLiteRepository

    root = tmp_path / "share_states/workflow_studio"
    root.mkdir(parents=True)
    db = sqlite3.connect(root / "studio.sqlite3")
    for sql in V001:
        db.execute(sql)
    db.execute("INSERT INTO studio_projects VALUES ('p','old','{}','active',1,NULL,'then','then')")
    db.execute("PRAGMA user_version=1")
    db.execute(f"PRAGMA application_id={APPLICATION_ID}")
    db.commit()
    db.close()
    with StudioService.open_workspace(tmp_path) as studio:
        assert studio.projects.get("p").name == "old"
        studio.sources.import_text("p", 1, "新來源")
    snapshot = next((root / "backups").glob("before-v001-*.sqlite3"))
    SQLiteRepository.restore(snapshot, tmp_path / "rollback")
    restored = sqlite3.connect(tmp_path / "rollback/studio.sqlite3")
    assert restored.execute("PRAGMA user_version").fetchone()[0] == 1
    assert restored.execute("SELECT name FROM studio_projects").fetchone()[0] == "old"
    restored.close()
