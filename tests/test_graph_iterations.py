import json

import pytest
from flow_base.graph.external import record_observation
from flow_base.graph.session import GraphSession
from flow_base.runtime import FlowError
from test_graph_builder import data_graph, import_graph
from test_graph_scheduler import setup


def loop_graph(each=True, *, limit=3):
    port = {"type": "string"}
    input_name = "item" if each else "value"
    output_name = "result" if each else "value"
    body = {
        "inputs": {input_name: port},
        "nodes": [
            {
                "id": "work",
                "kind": "task",
                "capability": "text.upper",
                "capability_version": "1",
                "permissions": [],
                "side_effect": "none",
                "inputs": {"source": {"source": "input://" + input_name, "port": port}},
                "outputs": {"value": port},
            }
        ],
        "outputs": {output_name: "artifact://work/value"},
    }
    node = {
        "id": "loop",
        "body": body,
        "limits": {
            "timeout_seconds": 120,
            "max_work": 10,
            "max_model_calls": 0,
            "max_cost_micro_usd": 0,
        },
    }
    graph = data_graph()
    if each:
        array = {"type": "array", "items": port}
        node.update(
            kind="foreach",
            items="input://rows",
            outputs={"results": array},
            max_items=limit,
            on_item_failure="failed",
        )
        graph.update(inputs={"rows": array}, outputs={"answer": "artifact://loop/results"})
        case = {"inputs": {"rows": ["a", "b"]}, "expected": {"answer": ["A", "B"]}}
    else:
        node.update(
            kind="repeat_until",
            initial={"source": "input://start", "port": port},
            outputs={"value": port},
            condition={"source": "artifact://work/value", "op": "equals", "value": "OK"},
            max_iterations=limit,
            on_limit="failed",
        )
        graph.update(inputs={"start": port}, outputs={"answer": "artifact://loop/value"})
        case = {"inputs": {"start": "ok"}, "expected": {"answer": "OK"}}
    return {**graph, "nodes": [node], "quality_contract": {"golden_cases": [case]}}


@pytest.mark.parametrize("rows", [[], ["a"], ["a", "a", "b"]])
def test_foreach_empty_single_and_bound_have_ordered_independent_ids(tmp_path, rows):
    rt, plan, bindings = setup(tmp_path, loop_graph())
    view = GraphSession.begin(rt, plan, {"inputs": {"rows": rows}}, tmp_path, bindings)
    assert view["outputs"] == {"answer": [v.upper() for v in rows]}
    items = view["nodes"]["loop"]["loop"]["items"]
    assert len({i["id"] for i in items}) == len(rows)
    assert [i["index"] for i in items] == list(range(len(rows)))


@pytest.mark.parametrize(
    "start,count,status", [("OK", 0, "complete"), ("ok", 1, "complete"), ("x", 2, "failed")]
)
def test_repeat_checks_quality_and_never_returns_success_only_because_limit_was_reached(
    tmp_path, start, count, status
):
    rt, plan, bindings = setup(tmp_path, loop_graph(False, limit=2))
    view = GraphSession.begin(rt, plan, {"inputs": {"start": start}}, tmp_path, bindings)
    assert view["status"] == status and view["nodes"]["loop"]["loop"]["index"] == count
    if status == "failed":
        assert view["nodes"]["loop"]["error"] == "MAX_ITERATIONS" and "outputs" not in view


@pytest.mark.parametrize("point", ["iteration_started", "iteration_finished"])
def test_restart_at_iteration_boundaries_does_not_duplicate_items(tmp_path, monkeypatch, point):
    rt, plan, bindings = setup(tmp_path, loop_graph())
    old = GraphSession.checkpoint

    def interrupted(self, state, node, event, **details):
        old(self, state, node, event, **details)
        if event == point:
            raise KeyboardInterrupt()

    with monkeypatch.context() as m:
        m.setattr(GraphSession, "checkpoint", interrupted)
        with pytest.raises(KeyboardInterrupt):
            GraphSession.begin(
                rt, plan, {"inputs": {"rows": ["a", "b"]}}, tmp_path, bindings, key="b" * 32
            )
    session = GraphSession(rt, "job_" + "b" * 32)
    final = session.interact("next", {})
    assert final["outputs"]["answer"] == ["A", "B"]
    items = final["nodes"]["loop"]["loop"]["items"]
    assert len(items) == 2 and final["nodes"]["loop"]["loop"]["spent"]["work"] == 2


def test_model_item_waits_for_complete_stream_and_metered_host_receipt(
    tmp_path, desktop_test_catalog
):
    graph = loop_graph()
    node = graph["nodes"][0]
    node["limits"].update(max_model_calls=1, max_cost_micro_usd=3)
    node["body"]["nodes"][0].update(
        capability="test-sink", permissions=["model"], side_effect="external"
    )
    catalog = json.loads(desktop_test_catalog.read_bytes())
    port = {"type": "string", "required": True}
    catalog["tools"]["test-sink"].update(
        inputs={"source": port},
        outputs={"value": port},
        permissions=["model"],
        side_effect="external",
        resources={"cost_micro_usd": 3, "model_calls": 1},
    )
    desktop_test_catalog.write_text(json.dumps(catalog))
    rt, plan, bindings = setup(tmp_path, graph)
    view = GraphSession.begin(rt, plan, {"inputs": {"rows": ["a", "b"]}}, tmp_path, bindings)
    session = GraphSession(rt, view["session_id"])
    task = view["pending"]
    view = session.interact(
        "authorize",
        {
            "revision": view["revision"],
            "task_id": task["id"],
            "scope_hash": task["scope_hash"],
            "payload_hash": task["payload_hash"],
            "user_text": "同意所列測試資料及額度",
        },
    )
    task = view["pending"]
    claimed = session.interact("claim", {"revision": view["revision"], "task_id": task["id"]})
    assert claimed["dispatch"]["idempotency_key"] == task["idempotency_key"]
    partial = session.interact(
        "stream_delta",
        {"revision": claimed["revision"], "task_id": task["id"], "sequence": 0, "text": "A"},
    )
    assert partial["event"]["text"] == "A" and "outputs" not in partial
    cursor = partial["pending"]
    record_observation(
        tmp_path,
        task["idempotency_key"],
        task["scope_hash"],
        "test-observation",
        "succeeded",
        {"value": "A"},
        usage={"cost_micro_usd": 2, "model_calls": 1},
    )
    with pytest.raises(FlowError, match="串流尚未完整"):
        session.interact("reconcile", {"revision": partial["revision"], "task_id": cursor["id"]})
    record_observation(
        tmp_path,
        task["idempotency_key"],
        task["scope_hash"],
        "test-observation",
        "succeeded",
        {"value": "A"},
        usage={"cost_micro_usd": 2, "model_calls": 1},
        stream={"count": 1, "completed": True, "hash": cursor["stream_hash"]},
    )
    final = session.interact(
        "reconcile", {"revision": partial["revision"], "task_id": cursor["id"]}
    )
    assert final["nodes"]["loop"]["error"] == "RESOURCE_LIMIT"
    assert final["nodes"]["loop"]["loop"]["spent"]["cost_micro_usd"] == 2
    assert not final["pending_tasks"] and len(final["nodes"]["loop"]["loop"]["items"]) == 1


def test_exported_loop_candidate_is_exercised_by_real_runtime(tmp_path):
    graph = loop_graph()
    svc, key, res = import_graph(tmp_path, graph)
    built = svc.install_workflow(draft_id=key, resolution=res)
    assert built["status"] == "verified" and built["evidence"]["provider_calls"] == 0


def test_cancel_during_iteration_keeps_finished_item_and_starts_no_more(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    rt, plan, bindings = setup(tmp_path, loop_graph())
    checkpoint = GraphSession.checkpoint
    paused, resume = Event(), Event()

    def observe(self, state, key, event, **details):
        checkpoint(self, state, key, event, **details)
        if event == "iteration_finished" and details["index"] == 0:
            paused.set()
            assert resume.wait(15)

    monkeypatch.setattr(GraphSession, "checkpoint", observe)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            GraphSession.begin,
            rt,
            plan,
            {"inputs": {"rows": ["a", "b"]}},
            tmp_path,
            bindings,
            key="c" * 32,
        )
        try:
            assert paused.wait(15)
            session = GraphSession(rt, "job_" + "c" * 32)
            current = session.interact("status", {})
            cancellation = session.interact(
                "cancel", {"revision": current["revision"], "request_id": "stop"}
            )
            assert cancellation["status"] == "cancel_requested"
        finally:
            resume.set()
        final = future.result(timeout=20)
    assert final["status"] == "cancelled"
    assert len(final["nodes"]["loop"]["loop"]["items"]) == 1


def test_over_item_bound_creates_no_child_work(tmp_path):
    rt, plan, bindings = setup(tmp_path, loop_graph(limit=1))
    final = GraphSession.begin(rt, plan, {"inputs": {"rows": ["a", "b"]}}, tmp_path, bindings)
    assert final["status"] == "failed"
    assert not final["nodes"]["loop"]["loop"]["items"]
    assert final["nodes"]["loop"]["error"] == "MAX_ITEMS"


def test_retry_resumes_only_the_failed_item(tmp_path, monkeypatch):
    rt, plan, bindings = setup(tmp_path, loop_graph())
    original = GraphSession.complete_node

    def broken(self, state, node, outputs, evidence):
        if node["id"] == "work" and state["inputs"]["item"] == "b":
            raise ValueError("fixture failure")
        return original(self, state, node, outputs, evidence)

    with monkeypatch.context() as m:
        m.setattr(GraphSession, "complete_node", broken)
        failed = GraphSession.begin(
            rt, plan, {"inputs": {"rows": ["a", "b", "c"]}}, tmp_path, bindings
        )
    first = failed["nodes"]["loop"]["loop"]["items"][0]
    session = GraphSession(rt, failed["session_id"])
    result = session.interact(
        "retry", {"revision": failed["revision"], "step": "loop", "reason": "修復第二筆"}
    )
    assert result["outputs"]["answer"] == ["A", "B", "C"]
    assert result["nodes"]["loop"]["loop"]["items"][0] == first
    assert result["nodes"]["loop"]["loop"]["spent"]["work"] == 4


def test_collect_errors_retains_original_item_order_and_failure(tmp_path, monkeypatch):
    graph = loop_graph()
    graph["nodes"][0].update(
        on_item_failure="collect_errors",
        outputs={"results": {"type": "array", "items": {"type": "object"}}},
    )
    rt, plan, bindings = setup(tmp_path, graph)
    old = GraphSession.complete_node

    def fail_middle(self, state, node, outputs, evidence):
        if node["id"] == "work" and state["inputs"]["item"] == "bad":
            raise ValueError("fixture")
        return old(self, state, node, outputs, evidence)

    monkeypatch.setattr(GraphSession, "complete_node", fail_middle)
    view = GraphSession.begin(rt, plan, {"inputs": {"rows": ["a", "bad", "z"]}}, tmp_path, bindings)
    assert [(r["status"], r["value"]) for r in view["outputs"]["answer"]] == [
        ("complete", "A"),
        ("failed", None),
        ("complete", "Z"),
    ]


def test_manual_correction_at_limit_requires_result_to_meet_quality_condition(tmp_path):
    graph = loop_graph(False, limit=1)
    graph["nodes"][0]["on_limit"] = "review_required"
    rt, plan, bindings = setup(tmp_path, graph)
    waiting = GraphSession.begin(rt, plan, {"inputs": {"start": "bad"}}, tmp_path, bindings)
    assert waiting["pending"]["kind"] == "iteration_review"
    session = GraphSession(rt, waiting["session_id"])
    reply = {
        "task_id": waiting["pending"]["id"],
        "revision": waiting["revision"],
        "evidence": {"user_text": "已修正結果", "decision": "accept_manual_result"},
    }
    with pytest.raises(FlowError):
        session.interact("complete", {**reply, "outputs": {"value": "BAD"}})
    assert session.interact("complete", {**reply, "outputs": {"value": "OK"}})["outputs"] == {
        "answer": "OK"
    }


def test_work_budget_is_independent_of_maximum_collection_length(tmp_path):
    graph = loop_graph(limit=5)
    graph["nodes"][0]["limits"]["max_work"] = 2
    rt, plan, bindings = setup(tmp_path, graph)
    result = GraphSession.begin(rt, plan, {"inputs": {"rows": ["a", "b", "c"]}}, tmp_path, bindings)
    assert result["nodes"]["loop"]["error"] == "RESOURCE_LIMIT"
    assert result["nodes"]["loop"]["loop"]["spent"]["work"] == 2


def test_expired_wall_clock_budget_starts_no_child(tmp_path, monkeypatch):
    from flow_base.graph import iterations

    rt, plan, bindings = setup(tmp_path, loop_graph())
    monkeypatch.setattr(iterations, "now", iter([0, 121]).__next__)
    view = GraphSession.begin(rt, plan, {"inputs": {"rows": ["a"]}}, tmp_path, bindings)
    assert (
        view["nodes"]["loop"]["error"] == "TIME_LIMIT"
        and not view["nodes"]["loop"]["loop"]["items"]
    )
