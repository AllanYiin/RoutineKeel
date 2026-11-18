import base64
import json

import pytest
from compiler_helpers import runtime_python
from test_guided_channels import Browser
from workflow_studio.contracts import StudioError
from workflow_studio.guided import agent_tools
from workflow_studio.guided.agent_tools import AgentTools
from workflow_studio.guided.local_page import LocalPage

pytest.importorskip("PIL", reason="Guided publish fixtures require Studio source dependencies")


def encode(text):
    return base64.b64encode(text.encode()).decode()


@pytest.fixture
def authoring(tmp_path, monkeypatch):
    # Same independent Runtime interpreter the other verifier tests use.
    monkeypatch.setattr(agent_tools, "find_runtime", lambda workspace: runtime_python())
    workspace = tmp_path / "workspace"
    page = LocalPage(workspace)
    agent = AgentTools(workspace, page.url)
    opened = agent.open(name="整理換行")
    person = Browser(page, opened["page_url"])
    folder = {
        "name": "案例",
        "files": [
            {"path": "input/a.txt", "data": encode("第一行\r\n第二行\r\n")},
            {"path": "output/a.txt", "data": encode("第一行\n第二行\n")},
        ],
    }
    status, data = person.post("/api/import", {"folder": folder})
    assert status == 200, data
    yield workspace, agent, person, opened["project_id"]
    page.close()


def draft(source, tool):
    # file.copy declares no format; text.normalize requires txt on both ends.
    port = {"type": "file"} if tool == "file.copy" else {"type": "file", "format": "txt"}
    return {
        "ir": {
            "workflow_id": "tidy-lines",
            "goal": {
                "description": "把文字檔的換行整理一致",
                "success_criteria": ["與案例成果相同"],
            },
            "inputs": {"source": port},
            "outputs": {"result": "artifact://tidy/result"},
            "evidence": [
                {
                    "id": "case",
                    "source_id": source["source_id"],
                    "source_version": source["version"],
                    "evidence_locator": "output/a.txt:1",
                    "claim": "成果的每一行內容與輸入相同",
                    "confidence_label": "observed",
                }
            ],
            "nodes": [
                {
                    "id": "tidy",
                    "tool": tool,
                    "inputs": {
                        "source": {
                            "source": "input://source",
                            "port": port,
                        }
                    },
                    "outputs": {"result": {**port, "path": "artifacts/a.txt"}},
                    "evidence_refs": ["case"],
                }
            ],
        }
    }


def test_failed_trial_is_repaired_then_person_publishes(authoring):
    workspace, agent, person, project_id = authoring
    source = agent.sources(project_id)[0]
    state = agent.submit_draft(
        project_id, agent.state(project_id)["version"], draft(source, "file.copy")
    )
    with pytest.raises(StudioError):
        agent.trial(project_id, state["version"])
    view = person.view()
    status, data = person.post(
        "/api/answer", {"version": view["version"], "card_id": "goal", "action": "confirm"}
    )
    status, data = person.post(
        "/api/commit", {"version": data["result"]["version"], "actor": "作者"}
    )
    view = data["result"]
    assert view["next"] == {"actor": "person", "action": "add_case"}
    assert view["case_suggestions"] == [
        {
            "source_id": source["source_id"],
            "input_path": "input/a.txt",
            "expected_path": "output/a.txt",
        }
    ]
    with pytest.raises(StudioError) as caught:
        agent.trial(project_id, view["version"])
    assert caught.value.code == "TEST_FAILED"
    status, data = person.post(
        "/api/add_case", {"version": view["version"], **view["case_suggestions"][0]}
    )
    assert status == 200 and data["result"]["case_suggestions"] == [], data
    assert data["result"]["next"] == {"actor": "agent", "action": "trial"}

    state = agent.trial(project_id, data["result"]["version"])
    assert state["stage"] == "trial_failed" and state["next"]["actor"] == "agent"
    assert (
        "第一行" in state["trial"]["cases"][0]["text_diff"]
        or state["trial"]["cases"][0]["text_diff"]
    )
    assert person.post("/api/publish", {"version": state["version"], "actor": "作者"})[0] == 400

    state = agent.submit_draft(project_id, state["version"], draft(source, "text.normalize"))
    assert state["stage"] == "committed", "unchanged confirmed statements need no second click"
    assert state["repairs"] == 1
    state = agent.trial(project_id, state["version"])
    assert state["stage"] == "trial_passed" and state["next"] == {
        "actor": "person",
        "action": "publish",
    }

    status, data = person.post("/api/publish", {"version": state["version"], "actor": "作者"})
    assert status == 200 and data["result"]["stage"] == "published", data
    manifest = json.loads((workspace / "tidy-lines/flow.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "tidy-lines"
    assert data["result"]["release"]["approved_by"] == "作者"
    assert agent.state(project_id)["next"] == {"actor": "nobody", "action": "done"}


def test_two_failed_repairs_hand_control_back_to_the_person(authoring):
    _, agent, person, project_id = authoring
    source = agent.sources(project_id)[0]
    state = agent.submit_draft(
        project_id, agent.state(project_id)["version"], draft(source, "file.copy")
    )
    view = person.view()
    status, data = person.post(
        "/api/answer", {"version": view["version"], "card_id": "goal", "action": "confirm"}
    )
    status, data = person.post(
        "/api/commit", {"version": data["result"]["version"], "actor": "作者"}
    )
    view = data["result"]
    status, data = person.post(
        "/api/add_case", {"version": view["version"], **view["case_suggestions"][0]}
    )
    state = agent.trial(project_id, data["result"]["version"])
    for _ in range(2):
        state = agent.submit_draft(project_id, state["version"], draft(source, "file.copy"))
        state = agent.trial(project_id, state["version"])
    assert state["stage"] == "trial_failed" and state["next"] == {
        "actor": "person",
        "action": "reopen",
    }
    with pytest.raises(StudioError) as caught:
        agent.submit_draft(project_id, state["version"], draft(source, "text.normalize"))
    assert caught.value.code == "TEST_FAILED"
    status, data = person.post("/api/reopen", {"version": state["version"]})
    assert status == 200 and data["result"]["next"] == {"actor": "agent", "action": "submit_draft"}
