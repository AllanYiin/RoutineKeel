import json

import pytest
from flow_base.graph.builder import build_graph
from flow_base.graph.capabilities import resolve_graph
from flow_base.graph.session import GraphSession
from flow_base.graph.source import graph_source
from flow_base.runtime import FlowError, Runtime, read_json
from test_graph_builder import data_graph


def diamond():
    graph = data_graph()
    b, a = graph["nodes"]
    c = {**b, "id": "other"}
    d = {
        **b,
        "id": "end",
        "depends_on": ["other"],
        "inputs": {"source": {"source": "artifact://upper/result", "port": {"type": "string"}}},
    }
    graph.update(nodes=[d, c, b, a], outputs={"result": "artifact://end/result"})
    return graph


def setup(tmp_path, graph):
    runtime = Runtime(tmp_path / "share_states")
    src = graph_source(graph, json.dumps(graph).encode())
    resolution = resolve_graph(src["graph"], tmp_path)
    plan, _, registry, _ = build_graph(
        src, {"capability_fingerprint": resolution["fingerprint"]}, None, tmp_path
    )
    return runtime, plan, registry["bindings"]


def test_diamond_revises_only_descendants_and_rejects_stale_or_duplicate_change(tmp_path):
    rt, plan, bindings = setup(tmp_path, diamond())
    view = GraphSession.begin(rt, plan, {"inputs": {"text": "v"}}, tmp_path, bindings)
    session = GraphSession(rt, view["session_id"])
    assert view["outputs"] == {"result": "V"}
    change = {
        "step": "upper",
        "reason": "重算此分支",
        "request_id": "redo",
        "revision": view["revision"],
    }
    after = session.interact("revise", change)
    assert {k: r["attempt"] for k, r in after["nodes"].items()} == {
        "copy": 1,
        "upper": 2,
        "other": 1,
        "end": 2,
    }
    assert session.interact("revise", change) == after
    with pytest.raises(FlowError):
        session.interact("revise", {**change, "step": "other"})
    assert [e["event"] for e in read_json(session.path)["audit"]].count("revise") == 1


def test_checkpoint_recovers_after_process_interruption_without_repeating_other_nodes(
    tmp_path, monkeypatch
):
    rt, plan, bindings = setup(tmp_path, diamond())
    original = GraphSession.execute

    def interrupted(self, state, node):
        if node["id"] == "end":
            raise KeyboardInterrupt()
        return original(self, state, node)

    with monkeypatch.context() as m:
        m.setattr(GraphSession, "execute", interrupted)
        with pytest.raises(KeyboardInterrupt):
            GraphSession.begin(
                rt, plan, {"inputs": {"text": "x"}}, tmp_path, bindings, key="1" * 32
            )
    session = GraphSession(rt, "job_" + "1" * 32)
    completed = session.interact("next", {})
    assert completed["outputs"] == {"result": "X"} and all(
        r["attempt"] == 1 for r in completed["nodes"].values()
    )


def test_multiple_pending_review_tasks_resume_independently_and_cancel(tmp_path):
    graph = data_graph()
    review = {
        "kind": "human_review",
        "id": "left",
        "prompt": "請檢視",
        "outputs": {"answer": {"type": "string"}},
    }
    graph.update(
        nodes=[review, {**review, "id": "right"}],
        outputs={"a": "artifact://left/answer", "b": "artifact://right/answer"},
    )
    rt, plan, bindings = setup(tmp_path, graph)
    view = GraphSession.begin(rt, plan, {"inputs": {"text": "x"}}, tmp_path, bindings)
    assert [p["step"] for p in view["pending_tasks"]] == ["left", "right"]
    session = GraphSession(rt, view["session_id"])
    task = view["pending_tasks"][1]
    payload = {
        "task_id": task["id"],
        "revision": view["revision"],
        "request_id": "reply",
        "outputs": {"answer": "通過"},
        "evidence": {"user_text": "通過"},
    }
    next_view = session.interact("complete", payload)
    assert len(next_view["pending_tasks"]) == 1 and next_view["pending"]["step"] == "left"
    assert session.interact("complete", payload) == next_view
    cancelled = session.interact(
        "cancel", {"revision": next_view["revision"], "request_id": "stop"}
    )
    assert cancelled["status"] == "cancelled" and not cancelled["pending_tasks"]
    assert cancelled["nodes"]["right"]["status"] == "succeeded"


def test_failed_node_is_retryable_and_keeps_the_original_error_in_audit(tmp_path, monkeypatch):
    rt, plan, bindings = setup(tmp_path, diamond())
    original = GraphSession.complete_node

    def fail_once(self, state, node, outputs, evidence):
        if node["id"] == "end":
            raise ValueError("fixture")
        return original(self, state, node, outputs, evidence)

    with monkeypatch.context() as m:
        m.setattr(GraphSession, "complete_node", fail_once)
        failed = GraphSession.begin(rt, plan, {"inputs": {"text": "x"}}, tmp_path, bindings)
    session = GraphSession(rt, failed["session_id"])
    assert failed["status"] == "failed"
    final = session.interact(
        "retry", {"step": "end", "revision": failed["revision"], "reason": "修復完成"}
    )
    assert final["outputs"] == {"result": "X"} and final["nodes"]["end"]["attempt"] == 2
    assert any(e["event"] == "failed" for e in read_json(session.path)["audit"])
