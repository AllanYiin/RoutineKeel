"""A verified replacement reserves one Run and resumes through its durable handoff."""

from contextlib import closing

import pytest
from flow_base.core.service import WorkflowService
from flow_base.execution.queue_worker import run_queue_worker
from flow_base.graph import continuation
from flow_base.graph.external import record_observation
from flow_base.graph.session import GraphSession
from flow_base.runtime import FlowError, read_json
from test_blueprint_completion import wait_completion
from test_capability_resolver import adapter, observed_inventory  # noqa: F401
from test_graph_builder import import_graph
from test_graph_review_external import approval, external_job
from test_toolchain_completion import report_graph


def _mapping(tool):
    text = {"type": "string", "required": True}
    row = adapter(tool, locus="local_runtime", cap="fixture.substitute")
    row["portable"].update(distribution=None, inputs={"source": text}, outputs={"result": text})
    row.update(
        inputs={"source": text},
        outputs={"result": text},
        preparation_allowed=True,
        preparation={
            "recipe_schema_version": 1,
            "id": tool,
            "version": "1.0.0",
            "action": "construct_mapping",
            "tool": tool,
            "platforms": row["platforms"],
            "outputs": {"result": {"source": "source"}},
        },
    )
    return row


def test_continuation_reuses_only_a_scoped_external_receipt(tmp_path, desktop_test_catalog):
    session, waiting = external_job(tmp_path, desktop_test_catalog)
    authorized = session.interact("authorize", approval(waiting))
    task = authorized["pending"]
    claimed = session.interact("claim", {"task_id": task["id"], "revision": authorized["revision"]})
    service = WorkflowService(tmp_path)
    unsettled = read_json(session.path)
    with pytest.raises(FlowError, match="仍有宿主回執待查"):
        continuation.preview(service, unsettled)
    record_observation(
        tmp_path,
        task["idempotency_key"],
        task["scope_hash"],
        "fixture-confirmed",
        "succeeded",
        {"result": "delivered"},
    )
    session.interact("reconcile", {"task_id": task["id"], "revision": claimed["revision"]})
    completed = read_json(session.path)
    saved = continuation._inherited(session, completed, completed["plan"], completed["bindings"])
    assert saved["copy"]["outputs"] == {"result": "delivered"}
    replacement = {
        "copy": {
            **completed["bindings"]["copy"],
            "destination": "https://changed.example",
        }
    }
    with pytest.raises(FlowError, match="不能重新派送"):
        continuation._inherited(session, completed, completed["plan"], replacement)
    completed.update(run_id="run_" + "2" * 32, rebind={"work_id": "completion_" + "3" * 32})
    scope = continuation._describe(
        completed,
        (
            {"install_hash": "fixture"},
            None,
            {},
            {},
            "candidate",
            replacement,
            completed["plan"],
        ),
    )
    assert scope["needs_approval"] and scope["differences"][0]["after"]["destinations"] == [
        "https://changed.example"
    ]
    previous = completed["bindings"]["copy"]
    higher_cost = {
        "copy": {
            **previous,
            "resources": {**previous["resources"], "cost_micro_usd": 1},
        }
    }
    priced_scope = continuation._describe(
        completed,
        ({"install_hash": "fixture"}, None, {}, {}, "candidate", higher_cost, completed["plan"]),
    )
    assert priced_scope["needs_approval"]
    assert priced_scope["differences"][0]["after"]["cost_micro_usd"] == 1


def _workflow():
    graph = report_graph()
    text = {"type": "string"}
    graph.update(
        graph_contract_version="2.3",
        inputs={"source": text},
        outputs={"result": "artifact://transform/result"},
    )
    graph["nodes"] = [
        {
            "id": "copy",
            "kind": "task",
            "capability": "text.copy",
            "capability_version": "1",
            "permissions": [],
            "side_effect": "none",
            "inputs": {"source": {"source": "input://source", "port": text}},
            "outputs": {"text": text},
        },
        {
            "id": "review",
            "kind": "human_review",
            "prompt": "請確認交接文字",
            "inputs": {"source": {"source": "artifact://copy/text", "port": text}},
            "outputs": {"text": text},
        },
        {
            "id": "transform",
            "kind": "task",
            "capability": "fixture.substitute",
            "capability_version": "1.0.0",
            "permissions": [],
            "side_effect": "none",
            "inputs": {"source": {"source": "artifact://review/text", "port": text}},
            "outputs": {"result": text},
            "capability_requirement": {
                "id": "substitute",
                "capability": "fixture.substitute",
                "version": "1.0.0",
                "required": True,
                "portability": "flexible",
            },
        },
    ]
    cases = [
        {"inputs": {"source": value}, "expected": {"result": value}}
        for value in ("original", "確認")
    ]
    graph["quality_contract"] = {
        "golden_cases": [
            {
                **case,
                "replies": [
                    {
                        "step": "review",
                        "kind": "human",
                        "outputs": {"text": case["inputs"]["source"]},
                    }
                ],
            }
            for case in cases
        ],
        "adapter_cases": {
            "transform": [{"id": f"case_{n}", **case} for n, case in enumerate(cases)]
        },
    }
    return graph


def test_rebind_activation_recovers_at_three_commit_boundaries(
    observed_inventory,  # noqa: F811
    monkeypatch,
):
    old, new = _mapping("fixture.first_mapping"), _mapping("fixture.next_mapping")
    workspace = observed_inventory(old)
    service, draft, _ = import_graph(workspace, _workflow())
    working = service.blueprint_completion(
        "start",
        payload={"draft_id": draft, "intent": "install", "request_id": "original"},
    )
    installed = wait_completion(service, working, ("installed", "needs_input"))
    assert installed["status"] == "installed", installed.get("gaps")
    installed_hash = service.workspace.task("toolchain_report", read_only=True).manifest.sha256
    source = workspace / "source.json"
    source.write_text('{"inputs":{"source":"runtime"}}', encoding="utf-8")
    submitted = service.submit_workflow({"task": "toolchain_report", "source": source})
    assert run_queue_worker(workspace, once=True, poll_interval=0.1)["processed"] == 1
    original_run = service.get_workflow_status(submitted.execution_id)["run_id"]
    session = "job_" + original_run.removeprefix("run_")
    original = service.blueprint_task(session, "status")
    pending = original["pending"]
    assert pending["step"] == "review"
    observed_inventory({**old, "allowed": False}, new)
    suspended = service.blueprint_task(
        session,
        "complete",
        {
            "revision": original["revision"],
            "task_id": pending["id"],
            "outputs": {"text": "approved runtime text"},
            "evidence": {"user_text": "同意此文字"},
        },
    )
    assert suspended["code"] == "REBIND_REQUIRED"
    waiting = service.get_workflow_status(submitted.execution_id)
    assert waiting["status"] == "waiting" and waiting["root_run_id"] == original_run
    assert not service.get_workflow_result(submitted.execution_id)["result_available"]
    repair = wait_completion(
        service,
        service.blueprint_completion("status", suspended["rebind_work_id"]),
        ("verified", "needs_input"),
    )
    assert repair["status"] == "verified", repair.get("gaps")
    scope = service.blueprint_task(session, "preview_rebind")
    assert not scope["needs_approval"]

    def refuse_unsafe_replay(*_args, **_kwargs):
        raise FlowError("REBIND_UNSAFE", "fixture result cannot be reused safely")

    with monkeypatch.context() as patch:
        patch.setattr(continuation, "_inherited", refuse_unsafe_replay)
        with pytest.raises(FlowError, match="fixture result cannot be reused safely"):
            service.blueprint_task(
                session,
                "activate_rebind",
                {"scope_hash": scope["scope_hash"], "revision": scope["source_checkpoint"]},
            )
    rejected = read_json(GraphSession(service.workspace.runtime, session).path)
    assert "handoff" not in rejected and "continuation" not in rejected

    def interrupt(*_args, **_kwargs):
        raise OSError("fixture handoff interruption")

    for point in ("reserve_workflow_run", "_seed", "run_workflow"):
        with monkeypatch.context() as patch:
            patch.setattr(continuation, point, interrupt)
            with pytest.raises(OSError, match="fixture handoff interruption"):
                service.blueprint_task(
                    session,
                    "activate_rebind",
                    {
                        "scope_hash": scope["scope_hash"],
                        "revision": scope["source_checkpoint"],
                    },
                )
        service = WorkflowService(workspace)

    resumed = service.blueprint_task(session, "next")
    assert resumed["status"] == "complete" and resumed["outputs"] == {
        "result": "approved runtime text"
    }
    assert resumed["root_run_id"] == resumed["parent_run_id"] == original_run
    assert (
        resumed["active_run_id"] != original_run and resumed["rebind_work_id"] == repair["work_id"]
    )
    for key in ("copy", "review"):
        assert resumed["nodes"][key]["inherited_from"]["run_id"] == original_run
    replay = service.blueprint_task(session, "activate_rebind", {"scope_hash": scope["scope_hash"]})
    assert replay["active_run_id"] == resumed["active_run_id"]
    assert (
        service.workspace.task("toolchain_report", read_only=True).manifest.sha256 == installed_hash
    )
    with closing(service.workspace.runtime.connect()) as db:
        rows = db.execute(
            "SELECT run_id, job_id, revision_id FROM run_executions WHERE run_id IN (?,?)",
            (resumed["active_run_id"], original_run),
        ).fetchall()
        assert len(rows) == 2 and len({row["job_id"] for row in rows}) == 1
        assert len({row["revision_id"] for row in rows}) == 2
    origin = read_json(GraphSession(service.workspace.runtime, session).path)
    assert origin["continuation"]["run_id"] == resumed["active_run_id"]
    result = service.get_workflow_result(submitted.execution_id)
    assert result["result_available"] and result["run_id"] == resumed["active_run_id"]
    assert result["root_run_id"] == original_run and any(
        row["path"] == "artifacts/results.json" for row in result["artifacts"]
    )

    later_source = workspace / "startup-drift.json"
    later_source.write_text('{"inputs":{"source":"later"}}', encoding="utf-8")
    planned = service.plan_workflow({"task": "toolchain_report", "source": later_source})
    assert planned.plan["blocked"][0]["recoverable_bindings"] is True
    later = service.submit_workflow({"task": "toolchain_report", "source": later_source})
    assert run_queue_worker(workspace, once=True, poll_interval=0.1)["processed"] == 1
    later_status = service.get_workflow_status(later.execution_id)
    assert later_status["status"] == "waiting" and later_status["wait_reason"] == "REBIND_REQUIRED"
    assert later_status["root_run_id"] != original_run and later_status["rebind_work_id"]
    later_repair = wait_completion(
        service,
        service.blueprint_completion("status", later_status["rebind_work_id"]),
        ("verified", "needs_input"),
    )
    assert later_repair["status"] == "verified"
