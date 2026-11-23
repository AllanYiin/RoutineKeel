"""Whole local desktop protocol. Chat answers are fixtures, with no real user or provider claims."""

import asyncio
import copy
import json
from pathlib import Path
from zipfile import ZipFile

from flow_base.core.service import WorkflowService
from flow_base.studio import studio_request
from test_graph_builder import data_graph
from test_mcp_stdio import call, client_for
from test_quality_feedback import decision
from test_workspace import cli


def golden(text):
    return {
        "inputs": {"text": text},
        "expected": {"original": text, "upper": text.upper(), "note": "ok"},
        "replies": [{"step": "check", "kind": "human", "outputs": {"note": "ok"}}],
    }


def test_desktop_interview_to_reversible_version(tmp_path, workflow_design_review):
    graph = data_graph()
    graph["nodes"][0]["capability"] = "text.copy"
    graph["nodes"].append(
        {
            "id": "check",
            "kind": "human_review",
            "prompt": "是否接受交付",
            "inputs": {
                "preview": {"source": "artifact://upper/result", "port": {"type": "string"}}
            },
            "outputs": {"note": {"type": "string"}},
        }
    )
    graph["outputs"]["note"] = "artifact://check/note"
    graph["quality_contract"].update(
        golden_cases=[golden("OK")], success_criteria=["原文及大寫，交付前一次校閱"]
    )

    def studio(op, **params):
        return studio_request(tmp_path, op, params)

    p = studio("create", name="文字校對", task=graph["workflow"]["goal"])
    pid = p["project_id"]
    q = studio("next", project_id=pid, domain="goal")
    p = studio(
        "reply",
        project_id=pid,
        expected_revision=q["revision"],
        request_id="reply",
        domain="goal",
        question_id=q["question_id"],
        user_text=graph["workflow"]["goal"],
    )
    p = studio("pause", project_id=pid, expected_revision=p["revision"], request_id="pause")
    assert studio("get", project_id=pid)["phase"] == "paused"
    p = studio("resume", project_id=pid, expected_revision=p["revision"], request_id="resume")
    design = studio(
        "prepare_blueprint",
        project_id=pid,
        expected_revision=p["revision"],
        request_id="design",
        graph=graph,
        design={"rules": "保留原文"},
    )
    design = studio("finalize_workflow_design", project_id=pid,
                    expected_revision=design["revision"], request_id="finalize",
                    draft_id=design["proposal_id"], review=workflow_design_review(design))
    export = studio(
        "export_blueprint",
        project_id=pid,
        expected_revision=design["revision"],
        request_id="export",
        proposal_id=design["proposal_id"],
        decision="approve",
        user_text="確認定稿並匯出",
        review_hash=design["review_hash"],
    )
    svc = WorkflowService(tmp_path)
    with ZipFile(export["path"]) as z:
        assert all(n.endswith(".json") for n in z.namelist())
    key = svc.install_workflow(archive=export["path"])["draft_id"]
    build = svc.install_workflow(
        draft_id=key,
        resolution={
            "capability_fingerprint": svc.resolve_blueprint_capabilities(key)["fingerprint"]
        },
    )
    assert build["validation"]["status"] == "PASS" and not svc.list_workflows()
    svc.install_workflow(
        draft_id=key, build_id=build["build_id"], install_hash=build["install_hash"]
    )
    source = tmp_path / "input.json"
    source.write_text(json.dumps({"inputs": {"text": "bad"}}))

    async def submit():
        async with client_for(tmp_path) as c:
            plan = (await call(c, "plan_workflow", workflow_id="graph_copy", source=str(source)))[
                "result"
            ]
            a = plan["plan"]["next_actions"][0]
            args = {**a["arguments"], "idempotency_key": "from-chat"}
            first = (await call(c, a["tool"], **args))["result"]
            assert (await call(c, a["tool"], **args))["result"]["execution_id"] == first[
                "execution_id"
            ]
            return first["execution_id"]

    eid = asyncio.run(submit())
    assert svc.agent_workflow(execution_id=eid)["status"] == "queued"
    cli(tmp_path, "queue-worker", "--once")
    view = svc.agent_workflow(execution_id=eid)
    sid = view["session_id"]
    assert len(view["pending"]) == 1 and view["pending"][0]["node_id"] == "check"
    job = svc.blueprint_task(
        sid,
        "complete",
        {
            "revision": view["revision"],
            "task_id": view["pending"][0]["id"],
            "outputs": {"note": "ok"},
            "evidence": {"user_text": "已看過，請記錄本次輸出"},
        },
    )
    assert job["outputs"]["upper"] == "bad"
    feedback, _ = decision(svc, sid, "correct", outputs=golden("bad")["expected"])
    proposal = svc.workflow_evolution(
        sid,
        "propose",
        {
            "feedback_ids": [feedback["feedback_id"]],
            "category": "logic",
            "hypothesis": "第二個工具只做複製",
            "impact": "符合大寫判準",
            "risk": "影響小寫輸入",
            "request_id": "fix",
        },
    )

    def evolve(state, op, **params):
        return svc.workflow_evolution(
            sid,
            op,
            dict(
                proposal_id=state["proposal_id"],
                expected_revision=state["revision"],
                request_id=op,
                **params,
            ),
        )

    candidate = copy.deepcopy(graph)
    candidate["workflow"].update(version="1.1.0", revision=2)
    candidate["nodes"][0]["capability"] = "text.upper"
    proposal = evolve(proposal, "prepare", graph=candidate, design={"rules": "保留原文"})
    proposal = evolve(proposal, "finalize", design_id=proposal["design_id"], review=workflow_design_review(proposal))
    proposal = evolve(proposal, "export", design_id=proposal["design_id"],
                      review_hash=proposal["review_hash"], user_text="確認定稿並匯出")
    held = tmp_path / "holdout.json"
    held.write_text(json.dumps([golden("fresh")]))
    compared = evolve(
        proposal,
        "compare",
        defect_cases=[{**golden("bad"), "feedback_id": feedback["feedback_id"]}],
        holdout_file=str(held),
        holdout_origin="工程測試獨立案例",
        unseen_by_proposer=True,
        limits={"review_count": 3},
    )
    assert (
        svc.agent_workflow(session_id=sid, proposal_id=proposal["proposal_id"])["phase"]
        == "compared"
    )
    upgraded = evolve(
        compared, "activate", comparison_hash=compared["comparison_hash"], user_text="採用這個版本"
    )
    assert svc.describe_workflow("graph_copy")["version"] == "1.1.0"
    assert evolve(upgraded, "rollback", user_text="還原測試版本")["phase"] == "rolled_back"
    assert (
        svc.describe_workflow("graph_copy")["version"] == "1.0.0" and Path(job["output"]).is_dir()
    )
