"""Retiring the web entry must not start it or discard existing workspace data."""

import json

import pytest
from workflow_studio.__main__ import main
from workflow_studio.service import StudioService


def test_retired_mcp_command_is_rejected_before_opening_workspace(tmp_path, capsys):
    workspace = tmp_path / "untouched"
    with pytest.raises(SystemExit) as caught:
        main(["--workspace", str(workspace), "mcp"])
    assert caught.value.code == 2
    assert "invalid choice" in capsys.readouterr().err
    assert not workspace.exists()


def test_old_session_data_survives_workspace_reopen(tmp_path):
    workspace = tmp_path / "studio"
    payload = json.dumps({"stage": "cards", "answers": {"goal": "confirmed"}})
    with StudioService.open_workspace(workspace) as studio:
        project = studio.projects.create("保留既有資料")
        with studio.repository.transaction() as db:
            db.execute(
                "INSERT INTO studio_guided_sessions VALUES (?,?,?,?)",
                (project.project_id, 1, payload, "2026-11-20T00:00:00+08:00"),
            )
    with StudioService.open_workspace(workspace) as studio:
        assert studio.projects.get(project.project_id).name == "保留既有資料"
        row = studio.repository.connection.execute(
            "SELECT version,state_json FROM studio_guided_sessions WHERE project_id=?",
            (project.project_id,),
        ).fetchone()
        assert tuple(row) == (1, payload)
