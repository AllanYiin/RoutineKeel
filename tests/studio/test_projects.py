import sqlite3

import pytest
from workflow_studio.contracts import StudioError
from workflow_studio.project.models import ProjectStatus
from workflow_studio.service import StudioService


@pytest.fixture
def studio(tmp_path):
    with StudioService.open_workspace(tmp_path) as service:
        yield service


def test_project_crud_reopen_and_runtime_separation(tmp_path):
    runtime = tmp_path / "share_states/state.sqlite3"
    runtime.parent.mkdir()
    runtime.write_bytes(b"untouched runtime")
    with StudioService.open_workspace(tmp_path) as studio:
        p = studio.projects.create("中文工作", {"success": "完整複製"})
        assert studio.projects.list() == [p]
        p = studio.projects.update(p.project_id, p.version, name="新版名稱")
        r = studio.projects.add_revision(p.project_id, p.version, {"nodes": []})
        p = studio.projects.get(p.project_id)
    with StudioService.open_workspace(tmp_path) as studio:
        assert studio.projects.get(p.project_id) == p
        assert studio.projects.get_revision(p.project_id, r.revision_id) == r
        p = studio.projects.archive(p.project_id, p.version)
        assert p.status == ProjectStatus.ARCHIVED
        assert studio.projects.list() == []
        assert studio.projects.list(include_archived=True) == [p]
        with pytest.raises(StudioError, match="恢復"):
            studio.projects.update(p.project_id, p.version, name="禁止編輯")
        p = studio.projects.delete(p.project_id, p.version)
        assert studio.projects.list(include_deleted=True) == [p]
        p = studio.projects.restore(p.project_id, p.version)
        assert studio.projects.list() == [p]
        assert studio.projects.get_revision(p.project_id, r.revision_id) == r
    assert runtime.read_bytes() == b"untouched runtime"


def test_cas_rejects_stale_writer_and_repeated_submission(tmp_path):
    with (
        StudioService.open_workspace(tmp_path) as first,
        StudioService.open_workspace(tmp_path) as second,
    ):
        p = first.projects.create("並行")
        first.projects.add_revision(p.project_id, p.version, {"value": 1})
        with pytest.raises(StudioError) as caught:
            second.projects.update(p.project_id, p.version, name="過期")
        assert caught.value.code == "REVISION_CONFLICT"
        with pytest.raises(StudioError):
            first.projects.add_revision(p.project_id, p.version, {"value": 1})
        assert len(first.projects.revisions(p.project_id)) == 1


def test_duplicate_names_are_independent_and_snapshots_do_not_mutate(studio):
    p = studio.projects.create("重複", {"list": [1]})
    other = studio.projects.create("重複")
    assert p.project_id != other.project_id
    p.goal["list"].append(2)
    assert studio.projects.get(p.project_id).goal == {"list": [1]}


def test_discard_draft_retains_history_and_skips_discarded_parent(studio):
    p = studio.projects.create("草稿")
    r1 = studio.projects.add_revision(p.project_id, p.version, {"value": 1})
    r2 = studio.projects.add_revision(p.project_id, 2, {"value": 2})
    p = studio.projects.discard_revision(p.project_id, r1.revision_id, 3)
    p = studio.projects.discard_revision(p.project_id, r2.revision_id, p.version)
    assert p.current_revision_id is None
    assert studio.projects.revisions(p.project_id) == []
    assert len(studio.projects.revisions(p.project_id, include_discarded=True)) == 2


def test_revision_content_and_published_state_are_immutable(studio):
    p = studio.projects.create("正式版本")
    r = studio.projects.add_revision(p.project_id, p.version, {"value": 1})
    db = studio.repository.connection
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE studio_revisions SET ir_json='{}'")
    # A fixture representing a version published by the later governance layer.
    db.execute("UPDATE studio_revisions SET status='published'")
    with pytest.raises(StudioError) as caught:
        studio.projects.discard_revision(p.project_id, r.revision_id, 2)
    assert caught.value.code == "REVISION_IMMUTABLE"
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        db.execute("UPDATE studio_revisions SET discarded=1")
    new = studio.projects.add_revision(p.project_id, 2, {"value": 2})
    assert new.parent_id == r.revision_id
    assert studio.projects.get_revision(p.project_id, r.revision_id).ir == {"value": 1}


def test_revision_reference_is_project_scoped(studio):
    p = studio.projects.create("甲")
    other = studio.projects.create("乙")
    r = studio.projects.add_revision(p.project_id, p.version, {})
    with pytest.raises(StudioError) as caught:
        studio.projects.get_revision(other.project_id, r.revision_id)
    assert caught.value.code == "REVISION_NOT_FOUND"


def test_atomic_revision_rolls_back_when_project_update_fails(studio):
    p = studio.projects.create("故障")
    studio.repository.connection.execute(
        """CREATE TRIGGER injected_failure BEFORE UPDATE ON studio_projects
        BEGIN SELECT RAISE(ABORT, 'injected failure'); END"""
    )
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        studio.projects.add_revision(p.project_id, p.version, {"value": 1})
    assert studio.projects.get(p.project_id) == p
    assert studio.projects.revisions(p.project_id) == []


@pytest.mark.parametrize(
    "name,goal", [("", {}), ("  ", {}), ("有效", []), ("有效", {"x": float("nan")})]
)
def test_invalid_project_does_not_persist(studio, name, goal):
    with pytest.raises(StudioError):
        studio.projects.create(name, goal)
    assert studio.projects.list() == []


def test_hundred_updates_reopen_without_corruption(tmp_path):
    for i in range(100):
        with StudioService.open_workspace(tmp_path) as studio:
            p = studio.projects.create(f"工作 {i}")
            studio.projects.add_revision(p.project_id, p.version, {"value": i})
    with StudioService.open_workspace(tmp_path) as studio:
        projects = studio.projects.list()
        assert len(projects) == 100
        for i, p in enumerate(projects):
            assert studio.projects.get_revision(p.project_id, p.current_revision_id).ir == {
                "value": i
            }
