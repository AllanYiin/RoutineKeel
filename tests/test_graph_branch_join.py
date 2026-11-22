import json

import pytest
from flow_base.graph.session import GraphSession
from flow_base.graph.source import inspect_graph
from flow_base.runtime import FlowError, read_json
from test_graph_builder import import_graph
from test_graph_contracts import example
from test_graph_scheduler import setup


def routed():
    graph = example()
    graph["quality_contract"] = {
        "golden_cases": [
            {"inputs": {"message": "x", "needs_review": False}, "expected": {"result": "x"}},
            {
                "inputs": {"message": "x", "needs_review": True},
                "expected": {"result": "改好"},
                "replies": [{"step": "manual", "kind": "human", "outputs": {"result": "改好"}}],
            },
        ]
    }
    return graph


def test_both_routes_replayed_by_installed_runtime_and_only_selected_gate_waits(tmp_path):
    service, key, res = import_graph(tmp_path, routed())
    build = service.install_workflow(draft_id=key, resolution=res)
    assert [len(c["simulated_host_and_review"]) for c in build["evidence"]["cases"]] == [0, 1]
    service.install_workflow(
        draft_id=key, build_id=build["build_id"], install_hash=build["install_hash"]
    )
    path = tmp_path / "job.json"
    path.write_text(json.dumps({"inputs": {"message": "data", "needs_review": False}}))
    report = service.run_workflow_sync({"task": "routing", "source": path})["blueprint"]
    assert report["outputs"] == {"result": "data"}
    assert (
        report["nodes"]["manual"]["status"] == "skipped"
        and report["nodes"]["manual"]["attempt"] == 0
    )
    assert report["nodes"]["route"]["selected_route"] == "false"


@pytest.mark.parametrize("data,expected", [({"flag": False}, "default"), ({}, "error")])
def test_typed_field_default_and_error_routes_survive_reopen(tmp_path, data, expected):
    graph = routed()
    graph["graph_contract_version"] = "2.2"
    graph["inputs"]["needs_review"] = {"type": "object"}
    join, _review, copy, switch = graph["nodes"]
    switch["condition"].update(field="flag", field_type="boolean")
    switch["routes"] = {"true": ["manual"], "default": ["copy"], "error": ["recover"]}
    switch["on_error"] = "route"
    graph["nodes"].append({**copy, "id": "recover"})
    join["branches"] = {
        "true": join["branches"]["true"],
        "default": join["branches"]["false"],
        "error": {"result": "artifact://recover/result"},
    }
    rt, plan, bindings = setup(tmp_path, graph)
    result = GraphSession.begin(
        rt, plan, {"inputs": {"needs_review": data, "message": "m"}}, tmp_path, bindings
    )
    assert result["nodes"]["route"]["selected_route"] == expected
    session = GraphSession(rt, result["session_id"])
    assert session.interact("next", {}) == result
    assert read_json(session.path)["nodes"]["route"]["attempt"] == 1
    del switch["routes"]["default"]
    with pytest.raises(FlowError):
        inspect_graph(graph)


def test_all_done_recovers_known_failure_only_with_declared_fallback(tmp_path, monkeypatch):
    graph = routed()
    copy = graph["nodes"][2]
    graph.update(
        graph_contract_version="2.2",
        nodes=[
            copy,
            {
                "id": "recover",
                "kind": "join",
                "policy": "all_done",
                "inputs": {
                    "result": {"source": "artifact://copy/result", "port": {"type": "string"}}
                },
                "fallbacks": {"result": "input://message"},
                "outputs": {"result": {"type": "string"}},
            },
        ],
        outputs={"result": "artifact://recover/result"},
    )
    rt, plan, bindings = setup(tmp_path, graph)
    original = GraphSession.execute

    def fail_copy(self, state, node):
        if node["id"] == "copy":
            raise ValueError("local fixture")
        return original(self, state, node)

    monkeypatch.setattr(GraphSession, "execute", fail_copy)
    result = GraphSession.begin(
        rt, plan, {"inputs": {"message": "保留原文", "needs_review": False}}, tmp_path, bindings
    )
    assert result["status"] == "complete" and result["outputs"] == {"result": "保留原文"}
    assert result["nodes"]["copy"]["status"] == "failed"
    assert result["nodes"]["recover"]["handled_failures"] == ["copy"]


def test_revising_a_decision_reselects_only_its_downstream_branch(tmp_path):
    graph = routed()
    del graph["inputs"]["needs_review"]
    switch = next(n for n in graph["nodes"] if n["kind"] == "switch")
    switch["condition"]["source"] = "artifact://choice/flag"
    graph["nodes"].append(
        {
            "id": "choice",
            "kind": "human_review",
            "prompt": "需要校閱嗎？",
            "outputs": {"flag": {"type": "boolean"}},
        }
    )
    rt, plan, bindings = setup(tmp_path, graph)
    view = GraphSession.begin(rt, plan, {"inputs": {"message": "測試"}}, tmp_path, bindings)
    session = GraphSession(rt, view["session_id"])

    def choose(view, value):
        return session.interact(
            "complete",
            {
                "task_id": view["pending"]["id"],
                "revision": view["revision"],
                "outputs": {"flag": value},
                "evidence": {"user_text": str(value)},
            },
        )

    completed = choose(view, False)
    assert completed["status"] == "complete" and completed["nodes"]["manual"]["attempt"] == 0
    revised = session.interact(
        "revise", {"step": "choice", "reason": "更改選擇", "revision": completed["revision"]}
    )
    waiting = choose(revised, True)
    assert (
        waiting["pending"]["step"] == "manual" and waiting["nodes"]["copy"]["status"] == "skipped"
    )
    assert waiting["nodes"]["route"]["attempt"] == 2
