import json

import pytest
from flow_base.graph.external import record_observation
from flow_base.graph.session import GraphSession
from flow_base.runtime import FlowError
from test_graph_builder import data_graph
from test_graph_scheduler import setup


def external_job(root, catalog):
    graph = data_graph()
    send = graph["nodes"][1]
    send.update(capability="test-sink", side_effect="external", permissions=["send"])
    graph.update(nodes=[send], outputs={"receipt": "artifact://copy/result"})
    data = json.loads(catalog.read_bytes())
    port = {"type": "string", "required": True}
    data["tools"]["test-sink"].update(
        inputs={"source": port},
        outputs={"result": port},
        side_effect="external",
        permissions=["send"],
        resources={"cost_micro_usd": 0},
    )
    catalog.write_text(json.dumps(data))
    rt, plan, bindings = setup(root, graph)
    view = GraphSession.begin(rt, plan, {"inputs": {"text": "fixture only"}}, root, bindings)
    return GraphSession(rt, view["session_id"]), view


def approval(view):
    task = view["pending"]
    return {
        "task_id": task["id"],
        "revision": view["revision"],
        "scope_hash": task["scope_hash"],
        "payload_hash": task["payload_hash"],
        "user_text": "同意將這份測試內容交給指定工具",
        "request_id": "approve-one",
    }


def test_scoped_chat_approval_claim_disconnect_and_independent_receipt(
    tmp_path, desktop_test_catalog
):
    session, view = external_job(tmp_path, desktop_test_catalog)
    assert (
        view["pending"]["kind"] == "authorize_external"
        and view["pending"]["scope"]["tool"] == "test-sink"
    )
    params = approval(view)
    with pytest.raises(FlowError):
        session.interact("authorize", {**params, "user_text": "", "actor": "approved"})
    with pytest.raises(FlowError):
        session.interact("authorize", {**params, "scope_hash": "different"})
    approved = session.interact("authorize", params)
    assert approved["pending"]["kind"] == "external"
    claim = {
        "task_id": approved["pending"]["id"],
        "revision": approved["revision"],
        "request_id": "claim-one",
    }
    claimed = session.interact("claim", claim)
    assert claimed["dispatch"]["arguments"] == {"source": "fixture only"}
    assert "dispatch" not in session.interact("claim", claim)
    resumed = GraphSession(session.runtime, session.id).interact("status", {})
    assert resumed["code"] == "RECONCILE_REQUIRED"
    task = resumed["pending"]
    cmd = {"task_id": task["id"], "revision": resumed["revision"], "request_id": "reconcile-one"}
    unknown = session.interact("reconcile", {**cmd, "evidence": {"receipt": "unverified"}})
    assert unknown["code"] == "RECONCILE_REQUIRED" and "outputs" not in unknown
    with pytest.raises(FlowError):
        session.interact(
            "revise", {"step": "copy", "reason": "重做", "revision": resumed["revision"]}
        )
    record_observation(
        tmp_path,
        task["idempotency_key"],
        task["scope_hash"],
        "fixture-provider-observation",
        "succeeded",
        {"result": "sent"},
    )
    completed = session.interact("reconcile", cmd)
    assert completed["outputs"] == {"receipt": "sent"}
    assert session.interact("reconcile", cmd) == completed
    with pytest.raises(FlowError):
        session.interact(
            "revise", {"step": "copy", "reason": "重送", "revision": completed["revision"]}
        )


def test_decline_never_dispatches_and_stale_reply_cannot_reverse_it(tmp_path, desktop_test_catalog):
    session, view = external_job(tmp_path, desktop_test_catalog)
    refused = session.interact("decline", {**approval(view), "user_text": "這次不傳送"})
    assert (
        refused["status"] == "failed" and not refused["pending_tasks"] and "dispatch" not in refused
    )
    with pytest.raises(FlowError):
        session.interact("authorize", approval(view))


def test_v1_actor_field_alone_cannot_grant_a_later_external_action(tmp_path, desktop_test_catalog):
    from test_blueprint_install import apply, candidate

    service, draft, built = candidate(tmp_path)
    apply(service, draft, built)
    path = tmp_path / "job.json"
    path.write_text(json.dumps({"inputs": {"text": "local fixture"}}))
    view = service.run_workflow_sync({"task": "example_flow", "source": path})["blueprint"]
    with pytest.raises(FlowError):
        service.blueprint_task(
            view["session_id"],
            "complete",
            {
                "task_id": view["pending"]["id"],
                "revision": view["revision"],
                "evidence": {"actor": "approved", "decision": "approved"},
            },
        )
    assert (
        service.blueprint_task(view["session_id"], "status")["pending"]["id"]
        == view["pending"]["id"]
    )


def test_trial_external_receipts_remain_inside_test_environment(tmp_path, desktop_test_catalog):
    from test_graph_builder import import_graph

    graph = data_graph()
    node = graph["nodes"][1]
    node.update(capability="test-sink", side_effect="external", permissions=["send"])
    graph.update(nodes=[node], outputs={"receipt": "artifact://copy/result"})
    graph["quality_contract"]["golden_cases"] = [
        {
            "inputs": {"text": "example"},
            "expected": {"receipt": "ok"},
            "replies": [
                {"step": "copy", "kind": "authorize_external"},
                {"step": "copy", "kind": "external", "outputs": {"result": "ok"}},
            ],
        }
    ]
    catalog = json.loads(desktop_test_catalog.read_bytes())
    port = {"type": "string", "required": True}
    catalog["tools"]["test-sink"].update(
        inputs={"source": port}, outputs={"result": port}, network=True
    )
    desktop_test_catalog.write_text(json.dumps(catalog))
    service, key, resolution = import_graph(tmp_path, graph)
    record = service.install_workflow(draft_id=key, resolution=resolution)
    assert record["status"] == "verified" and record["evidence"]["provider_calls"] == 0
    assert not (desktop_test_catalog.parent / "receipts").exists()
