"""Exercise the Studio design gates and approved failure policies through the Runtime."""

import copy
import json
from zipfile import ZipFile

import pytest
from flow_base.core.service import WorkflowService
from flow_base.runtime import FlowError
from flow_base.studio import studio_request
from routinekeel_contracts.errors import StudioError
from routinekeel_contracts.graph_validator import validate_blueprint
from test_graph_builder import data_graph
from test_quality_feedback import decision


def sketch(workspace, graph):
    project = studio_request(
        workspace, "create", {"name": "設計規範", "task": graph["workflow"]["goal"]}
    )
    return studio_request(
        workspace,
        "prepare_blueprint",
        {
            "project_id": project["project_id"],
            "expected_revision": project["revision"],
            "request_id": "draft",
            "graph": graph,
            "design": {"rules": "保持來源內容"},
        },
    )


def finish(workspace, draft, configuration, request_id="finalize"):
    return studio_request(
        workspace,
        "finalize_workflow_design",
        {
            "project_id": draft["project_id"],
            "expected_revision": draft["revision"],
            "request_id": request_id,
            "draft_id": draft["proposal_id"],
            "review": configuration,
        },
    )


def approve(workspace, proposal, **overrides):
    return studio_request(
        workspace,
        "export_blueprint",
        {
            "project_id": proposal["project_id"],
            "expected_revision": proposal["revision"],
            "request_id": "export",
            "proposal_id": proposal["proposal_id"],
            "user_text": "同意定稿並匯出",
            "decision": "approve",
            "review_hash": proposal.get("review_hash"),
            **overrides,
        },
    )


def install(workspace, export):
    service = WorkflowService(workspace)
    key = service.install_workflow(archive=export["path"])["draft_id"]
    resolution = service.resolve_blueprint_capabilities(key)
    verified = service.install_workflow(
        draft_id=key, resolution={"capability_fingerprint": resolution["fingerprint"]}
    )
    service.install_workflow(
        draft_id=key,
        build_id=verified["build_id"],
        install_hash=verified["install_hash"],
    )
    return service


def test_final_review_requires_complete_researched_inventory_and_exact_confirmation(
    tmp_path,
    workflow_design_review,
):
    graph = data_graph()
    graph["quality_contract"]["success_criteria"] = ["保留原始文字"]
    graph["nodes"][0].update(
        capability="model.example", parameters={"temperature": 0.2}
    )
    draft = sketch(tmp_path, graph)
    assert draft["status"] == "design_review_required"
    assert "底座" not in draft["draft_design"]["diagram"]
    assert "parameter/upper/temperature" in {
        request["variable"] for request in draft["draft_design"]["research_requests"]
    }
    with pytest.raises(FlowError, match="完整定稿"):
        approve(tmp_path, draft, request_id="early")
    review = workflow_design_review(draft)
    record = review["variables"]["parameter/upper/temperature"].pop("research")
    with pytest.raises(FlowError, match="網路研究紀錄"):
        finish(tmp_path, draft, review, request_id="missing-research")
    record["recommended_value"] = 0.9
    review["variables"]["parameter/upper/temperature"]["research"] = record
    with pytest.raises(FlowError, match="網路研究紀錄"):
        finish(tmp_path, draft, review, request_id="wrong-value")
    record["recommended_value"] = 0.2
    original_excerpt = record["excerpt"]
    record["excerpt"] = "A fabricated source recommends 0.2."
    with pytest.raises(FlowError, match="查核引文"):
        finish(tmp_path, draft, review, request_id="fabricated-citation")
    record["excerpt"] = original_excerpt
    review["step_decisions"]["upper"]["critical"] = True
    with pytest.raises(FlowError, match="關鍵的步驟"):
        finish(tmp_path, draft, review, request_id="uncovered-critical-node")
    review["step_decisions"]["upper"]["critical"] = False
    review["variables"]["parameter/upper/temperature"]["kind"] = "adjustable"
    review["glossary"] = {"temperature": "取樣溫度"}
    final = finish(tmp_path, draft, review)
    shown = final["design_review"]
    assert "底座 · 檢核輸入" in shown["final_diagram"]
    assert "底座 · 檢核與交付輸出" in shown["final_diagram"]
    assert "機器檢核" in shown["final_diagram"]
    assert shown["tools"]["upper"]["parameters"]["temperature"] == 0.2
    assert shown["glossary"] == review["glossary"]
    assert shown["variables"]["parameter/upper/temperature"]["research"] == record
    assert finish(tmp_path, draft, review) == final
    with pytest.raises(FlowError, match="定稿指紋"):
        approve(tmp_path, final, review_hash="another", request_id="wrong-design")
    with pytest.raises(FlowError, match="拒絕"):
        approve(tmp_path, final, user_text="先不要匯出", request_id="negative-reply")
    with pytest.raises(FlowError, match="明確核准"):
        approve(tmp_path, final, user_text="我還在看", request_id="pending-reply")
    assert not (tmp_path / "blueprints").exists()
    exported = approve(tmp_path, final)
    with ZipFile(exported["path"]) as archive:
        saved = json.loads(archive.read("design/blueprint.json"))
    assert saved["provenance"]["design_review"]["sha256"] == final["review_hash"]
    changed_rules = copy.deepcopy(saved)
    changed_rules["provenance"]["design"]["rules"] = "刪除來源內容"
    with pytest.raises(StudioError, match="定稿指紋"):
        validate_blueprint(changed_rules)
    saved["quality_contract"]["evaluators"] = [{"id": "added_after_review"}]
    with pytest.raises(StudioError, match="定稿內容"):
        validate_blueprint(saved)
    saved["quality_contract"].pop("evaluators")
    saved["nodes"][0]["capability"] = "text.upper"
    with pytest.raises(StudioError, match="定稿內容"):
        validate_blueprint(saved)


@pytest.mark.parametrize("tolerate", [False, True])
def test_approved_failure_policy_stops_or_delivers_with_follow_up(
    tmp_path, workflow_design_review, tolerate
):
    graph = data_graph()
    extra = dict(
        next(node for node in graph["nodes"] if node["id"] == "copy"), id="parallel"
    )
    graph["nodes"].append(extra)
    graph["outputs"]["parallel"] = "artifact://parallel/result"
    graph["quality_contract"]["golden_cases"][0]["expected"]["parallel"] = "ok"
    graph["quality_contract"]["success_criteria"] = ["原文及大寫"]
    draft = sketch(tmp_path, graph)
    review = workflow_design_review(draft)
    rule = review["policy"]["critical_steps"]["copy"]["machine"][0]
    rule["min"] = 2
    review["variables"]["check/copy/format/min"]["research"]["recommended_value"] = 2
    if tolerate:
        rule.update(tolerate=True, follow_up="人工檢查過短文字")
    else:
        review["policy"]["mode"] = "stop_on_error"
    final = finish(tmp_path, draft, review)
    service = install(tmp_path, approve(tmp_path, final))
    payload = tmp_path / "input.json"
    payload.write_text(json.dumps({"inputs": {"text": "x"}}))
    run = service.run_workflow_sync({"task": "graph_copy", "source": payload})
    state = run["blueprint"]
    if tolerate:
        assert state["outputs"]["upper"] == "X"
        quality = service.workflow_quality(state["session_id"])
        issue = next(
            item for item in quality["checks"] if item["id"] == "step/copy/format"
        )
        assert issue["status"] == "fail" and issue["follow_up"] == rule["follow_up"]
        assert state["pending_corrections"] == [issue]
        assert state["outputs"]["parallel"] == "x"
        assert run["graph_result_status"] == "accepted"
    else:
        assert state["status"] == "failed"
        assert state["nodes"]["copy"]["error"] == "WORKFLOW_CHECK_FAILED"
        assert state["nodes"]["upper"]["status"] == "pending"
        assert state["nodes"]["parallel"]["status"] == "pending"
        assert "outputs" not in state


def test_human_check_guards_delivery_and_can_reject(tmp_path, workflow_design_review):
    graph = data_graph()
    graph["nodes"] = [node for node in graph["nodes"] if node["id"] == "copy"]
    port = {"type": "string"}
    graph["nodes"].append(
        {
            "id": "review",
            "kind": "human_review",
            "prompt": "檢查並核准交付",
            "inputs": {"text": {"source": "artifact://copy/result", "port": port}},
            "outputs": {"result": port},
        }
    )
    graph["outputs"] = {"result": "artifact://review/result"}
    graph["quality_contract"] = {
        "success_criteria": ["由人確認文字"],
        "golden_cases": [
            {
                "inputs": {"text": "ok"},
                "expected": {"result": "ok"},
                "replies": [
                    {"step": "review", "kind": "human", "outputs": {"result": "ok"}}
                ],
            }
        ],
    }
    graph["outputs"]["unchecked"] = "artifact://copy/result"
    draft = sketch(tmp_path, graph)
    review = workflow_design_review(draft)
    review["policy"]["critical_steps"]["copy"]["human"] = "review"
    with pytest.raises(FlowError, match="人工校閱"):
        finish(tmp_path, draft, review, request_id="bypass")
    del graph["outputs"]["unchecked"]
    draft = studio_request(
        tmp_path,
        "prepare_blueprint",
        {
            "project_id": draft["project_id"],
            "expected_revision": draft["revision"],
            "request_id": "repair-draft",
            "graph": graph,
            "design": {"rules": "保持來源內容"},
        },
    )
    review = workflow_design_review(draft)
    review["policy"]["critical_steps"]["copy"]["human"] = "review"
    final = finish(tmp_path, draft, review)
    assert "人工等待" in final["design_review"]["final_diagram"]
    service = install(tmp_path, approve(tmp_path, final))
    payload = tmp_path / "needs-review.json"
    payload.write_text(json.dumps({"inputs": {"text": "待檢核"}}))
    state = service.run_workflow_sync({"task": "graph_copy", "source": payload})[
        "blueprint"
    ]
    assert state["status"] == "waiting" and state["pending"]["kind"] == "human"
    denied = service.blueprint_task(
        state["session_id"],
        "decline",
        {
            "revision": state["revision"],
            "task_id": state["pending"]["id"],
            "evidence": {"user_text": "這次檢核未通過"},
        },
    )
    assert (
        denied["status"] == "failed"
        and denied["nodes"]["review"]["error"] == "REVIEW_DECLINED"
    )


def test_corrected_delivery_rechecks_original_producer_and_keeps_official_output(
    tmp_path,
    workflow_design_review,
):
    from pathlib import Path

    from flow_base.storage.delivery import delivery_root

    graph = data_graph()
    graph["quality_contract"]["success_criteria"] = ["原文長度至少兩字"]
    port = {"type": "string"}
    graph["nodes"].append({
        "id": "package", "kind": "join", "policy": "all_success",
        "inputs": {"original": {"source": "artifact://copy/result", "port": port}},
        "outputs": {"original": port},
    })
    graph["outputs"]["original"] = "artifact://package/original"
    draft = sketch(tmp_path, graph)
    review = workflow_design_review(draft)
    review["policy"]["critical_steps"]["copy"]["machine"][0]["min"] = 2
    review["variables"]["check/copy/format/min"]["research"]["recommended_value"] = 2
    service = install(tmp_path, approve(tmp_path, finish(tmp_path, draft, review)))
    payload = tmp_path / "input.json"
    payload.write_text(json.dumps({"inputs": {"text": "ok"}}))
    run = service.run_workflow_sync({"task": "graph_copy", "source": payload})
    assert run["delivery_status"] == "published"
    sid = run["blueprint"]["session_id"]
    pointer = delivery_root(Path(run["output"])) / "current.json"
    original = pointer.read_bytes()
    event, _ = decision(
        service, sid, "correct", outputs={"original": "", "upper": "OK"}
    )
    assert event["after"]["result_status"] == "rejected"
    assert any(
        row["id"] == "step/copy/format" and row["status"] == "fail"
        for row in event["after"]["checks"]
    )
    with pytest.raises(FlowError, match="硬性條件"):
        decision(service, sid, "accept")
    assert pointer.read_bytes() == original


def test_reviewed_tool_location_must_match_runtime_binding(
    tmp_path, workflow_design_review
):
    graph = data_graph()
    graph["quality_contract"]["success_criteria"] = ["保留原始文字"]
    draft = sketch(tmp_path, graph)
    review = workflow_design_review(draft)
    review["tool_locations"]["copy"] = "external_service"
    final = finish(tmp_path, draft, review)
    assert final["build_preflight"]["status"] == "blocked"
    assert any(
        row["node"] == "copy" for row in final["build_preflight"]["capability_gaps"]
    )
    exported = approve(tmp_path, final)
    service = WorkflowService(tmp_path)
    key = service.install_workflow(archive=exported["path"])["draft_id"]
    assert service.resolve_blueprint_capabilities(key)["status"] == "blocked"
