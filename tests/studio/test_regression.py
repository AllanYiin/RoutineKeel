import time

import pytest
from gate_evidence import write_evidence
from workflow_studio.contracts import StudioError
from workflow_studio.service import StudioService


def test_hundred_create_update_reopen_cycles_preserve_all_revisions(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("CRUD must not invoke a model")

    monkeypatch.setattr("workflow_studio.llm.provider.ChatCompletionsProvider.stream", forbidden)
    snapshots = []
    start = time.perf_counter()
    for index in range(100):
        with StudioService.open_workspace(tmp_path / "studio") as studio:
            project = studio.projects.create(
                f"固定工作 {index:03d}",
                {"description": f"goal-{index}", "success_criteria": [f"result-{index}"]},
            )
            initial = studio.projects.add_revision(
                project.project_id,
                project.version,
                {"fixture": index, "values": [index, index + 1]},
            )
            current = studio.projects.get(project.project_id)
            updated = studio.projects.update(
                project.project_id,
                current.version,
                name=f"修改後 {index:03d}",
                goal={
                    "description": f"updated-{index}",
                    "success_criteria": ["preserve prior revision"],
                },
            )
            with pytest.raises(StudioError):
                studio.projects.update(project.project_id, project.version, name="stale must fail")
            snapshots.append((updated, initial))
        with StudioService.open_workspace(tmp_path / "studio") as reopened:
            assert reopened.projects.get(project.project_id) == updated
            assert (
                reopened.projects.get_revision(project.project_id, initial.revision_id) == initial
            )
    with StudioService.open_workspace(tmp_path / "studio") as studio:
        assert len(studio.projects.list()) == 100
        for project, revision in snapshots:
            assert studio.projects.get(project.project_id) == project
            assert (
                studio.projects.get_revision(project.project_id, revision.revision_id) == revision
            )
        assert studio.repository.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert studio.repository.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            studio.repository.connection.execute(
                "SELECT count(*) FROM studio_llm_calls"
            ).fetchone()[0]
            == 0
        )
    write_evidence(
        tmp_path,
        "crud-100",
        {
            "cycles": 100,
            "connections_reopened": 200,
            "projects_verified": 100,
            "revisions_verified": 100,
            "stale_updates_rejected": 100,
            "integrity_check": "ok",
            "foreign_key_errors": 0,
            "registered_model_calls": 0,
            "elapsed_seconds": time.perf_counter() - start,
            "passed": True,
        },
    )
