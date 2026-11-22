"""Versioned golden inputs use the real scheduler and retain explicit failure outcomes."""

from pathlib import Path

import pytest
from flow_base.graph.session import GraphSession
from flow_base.runtime import FlowError, digest, read_json
from test_graph_branch_join import routed
from test_graph_builder import data_graph, import_graph
from test_graph_iterations import loop_graph
from test_graph_scheduler import diamond, setup


def corpus():
    multi = data_graph()
    multi["nodes"][0]["inputs"]["source"]["source"] = "input://text"
    return {
        "dag": (diamond(), {"text": "abc"}, "complete", {"result": "ABC"}),
        "automatic_branch": (
            routed(),
            {"message": "original", "needs_review": False},
            "complete",
            {"result": "original"},
        ),
        "review_branch": (routed(), {"message": "original", "needs_review": True}, "waiting", None),
        "declined_branch": (routed(), {"message": "decline", "needs_review": True}, "failed", None),
        "foreach": (loop_graph(), {"rows": ["a", "b"]}, "complete", {"answer": ["A", "B"]}),
        "repeat_limit": (loop_graph(False, limit=2), {"start": "fail"}, "failed", None),
        "two_terminals": (multi, {"text": "xy"}, "complete", {"original": "xy", "upper": "XY"}),
    }


@pytest.mark.parametrize("name", list(corpus()))
def test_pinned_golden_case(tmp_path, name):
    graph, inputs, status, outputs = corpus()[name]
    locked = read_json(Path(__file__).with_name("fixtures") / "rk_next_corpus.json")
    assert digest([graph, inputs, status, outputs]) == locked[name]
    rt, plan, b = setup(tmp_path, graph)
    view = GraphSession.begin(rt, plan, {"inputs": inputs}, tmp_path, b)
    session = GraphSession(rt, view["session_id"])
    if name == "declined_branch":
        view = session.interact(
            "decline",
            {
                "revision": view["revision"], "task_id": view["pending"]["id"], "user_text": "拒絕本次交付"
            },
        )
    assert view["status"] == status and view.get("outputs") == outputs
    assert GraphSession(rt, session.id).interact("status", {})["revision"] == view["revision"]


@pytest.mark.parametrize("fault", ["missing_validator", "bad_expected", "changed_capability"])
def test_failure_never_creates_formal_install(tmp_path, monkeypatch, fault):
    graph = data_graph()
    if fault == "bad_expected":
        graph["quality_contract"]["golden_cases"][0]["expected"]["upper"] = "wrong"
    svc, key, res = import_graph(tmp_path, graph)
    if fault == "missing_validator":
        monkeypatch.setattr(
            "flow_base.blueprints.installer.check_with_studio",
            lambda *_: {"status": "NOT_RUN", "reason": "故障注入"},
        )
    if fault == "changed_capability":
        res["capability_fingerprint"] = "old"
    with pytest.raises(FlowError):
        svc.install_workflow(draft_id=key, resolution=res)
    assert not svc.list_workflows()
    assert svc.install_workflow(draft_id=key)["builds"][-1]["status"] == "blocked"
