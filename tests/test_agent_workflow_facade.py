import asyncio
import json

from flow_base.core.service import WorkflowService
from test_graph_builder import data_graph, import_graph
from test_mcp_stdio import call, client_for
from test_workspace import cli


def test_agent_reopens_compared_candidate_without_activating_it(tmp_path, monkeypatch):
    svc = WorkflowService(tmp_path)

    def saved(sid, op, args):
        assert (sid, op, args) == ("source-session", "get", {"proposal_id": "proposal"})
        return {
            "phase": "compared",
            "revision": 5,
            "comparison": {"fixed_count": 1, "defect_count": 1, "holdout_count": 1},
            "comparison_hash": "verified-comparison",
            "hypothesis": "修正文字",
            "expected_impact": "恢復原判準",
            "risk": "輸出改變",
        }

    monkeypatch.setattr(svc, "workflow_evolution", saved)
    view = svc.agent_workflow(session_id="source-session", proposal_id="proposal")
    assert view["phase"] == "compared" and view["comparison"]["holdout_count"] == 1
    activate = next(o for o in view["next_actions"] if o["arguments"]["operation"] == "activate")
    assert activate["arguments"]["payload"] == {
        "proposal_id": "proposal",
        "expected_revision": 5,
        "comparison_hash": "verified-comparison",
    }


def test_conversation_discovery_and_reconnect_use_persisted_ids(tmp_path):
    graph = data_graph()
    graph["nodes"].append(
        {
            "kind": "human_review",
            "id": "signoff",
            "prompt": "確認最後的成品",
            "inputs": {
                "preview": {"source": "artifact://upper/result", "port": {"type": "string"}}
            },
            "outputs": {"note": {"type": "string"}},
        }
    )
    graph["outputs"]["note"] = "artifact://signoff/note"
    golden = graph["quality_contract"]["golden_cases"][0]
    golden["expected"]["note"] = "完成"
    golden["replies"] = [{"step": "signoff", "kind": "human", "outputs": {"note": "完成"}}]
    svc, key, res = import_graph(tmp_path, graph)
    build = svc.install_workflow(draft_id=key, resolution=res)
    assert not svc.agent_workflow()["workflows"]
    svc.install_workflow(
        draft_id=key, build_id=build["build_id"], install_hash=build["install_hash"]
    )
    source = tmp_path / "job-input.json"
    source.write_text(json.dumps({"inputs": {"text": "hello"}}))

    async def submit():
        async with client_for(tmp_path) as client:
            catalog = (await call(client, "agent_workflow"))["result"]["workflows"]
            info = catalog[0]["invocation"]
            assert info == svc.describe_workflow("graph_copy")["invocation"]
            assert info["inputs"]["text"]["type"] == "string"
            assert info["human_gates"] == [{"node_id": "signoff", "reason": "human_review"}]
            assert info["resource_upper_bound"]["cost_micro_usd"] == 0
            p = (await call(client, "plan_workflow", workflow_id="graph_copy", source=str(source)))[
                "result"
            ]
            submit = p["plan"]["next_actions"][0]
            assert submit["tool"] == "submit_workflow" and p["plan"]["invocation"] == info
            queue = (await call(client, submit["tool"], **submit["arguments"]))["result"]
            return queue["execution_id"]

    eid = asyncio.run(submit())
    queued = cli(tmp_path, "agent-workflow", "--execution-id", eid)["result"]
    assert queued["status"] == "queued" and queued["run_id"] is None and queued["progress"] is None
    cli(tmp_path, "queue-worker", "--once")
    waiting = WorkflowService(tmp_path).agent_workflow(execution_id=eid)
    assert waiting["status"] == "succeeded" and waiting["session_status"] == "waiting"
    assert waiting["pending"][0]["node_id"] == "signoff"

    async def reconnect():
        async with client_for(tmp_path) as client:
            assert (await call(client, "agent_workflow", execution_id=eid))["result"] == waiting
            next_action = waiting["next_actions"][0]
            args = next_action["arguments"]
            args["payload"].update(
                outputs={"note": "確認"},
                evidence={
                    "actor": "desktop_user",
                    "user_text": "確認這個結果",
                    "decision": "approved",
                },
                request_id="chat-approval",
            )
            done = (await call(client, next_action["tool"], **args))["result"]
            assert done["outputs"]["upper"] == "HELLO"
            after = (await call(client, "agent_workflow", session_id=waiting["session_id"]))[
                "result"
            ]
            assert after["session_status"] == "complete" and not after["pending"]
            assert after["next_actions"][0]["tool"] == "workflow_quality"

    asyncio.run(reconnect())
