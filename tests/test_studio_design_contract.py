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


def section_hashes(proposal):
    return {key: section["sha256"] for key, section in proposal["review_sections"].items()}


def finish(workspace, draft, configuration, request_id="finalize", **overrides):
    return studio_request(
        workspace,
        "finalize_workflow_design",
        {
            "project_id": draft["project_id"],
            "expected_revision": draft["revision"],
            "request_id": request_id,
            "draft_id": draft["proposal_id"],
            "review": configuration,
            "draft_reply": "初稿可進入定稿",
            "shown_sections": section_hashes(draft),
            **overrides,
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
            "shown_sections": section_hashes(proposal),
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
    pid = draft["project_id"]
    resumed = studio_request(tmp_path, "next_batch", {"project_id": pid})
    assert resumed["design_progress"]["stage"] == "draft_review_required"
    assert resumed["questions"] == []
    assert "待校閱的設計提案" in resumed["text"]
    assert "保持來源內容" in resumed["text"]
    with pytest.raises(FlowError, match="完整定稿"):
        approve(tmp_path, draft, request_id="early")
    review = workflow_design_review(draft)
    for index, reply in enumerate((None, "先不要定稿", "確認定稿並匯出")):
        with pytest.raises(FlowError, match="初稿.*實際回覆"):
            finish(tmp_path, draft, review, request_id=f"no-draft-review-{index}", draft_reply=reply)
    omitted = section_hashes(draft)
    del omitted["implementation"]
    with pytest.raises(FlowError, match="技能、工具與變數"):
        finish(tmp_path, draft, review, request_id="omitted-section", shown_sections=omitted)
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
    assert final["draft_confirmation"] == {
        "draft_id": draft["proposal_id"], "revision": draft["revision"],
        "user_text": "初稿可進入定稿",
        "shown_sections": section_hashes(draft),
    }
    resumed = studio_request(tmp_path, "next_batch", {"project_id": pid})
    assert resumed["design_progress"]["stage"] == "final_review_required"
    assert resumed["questions"] == [] and resumed["next_actions"][0] == "export_blueprint"
    shown = final["design_review"]
    assert "底座 · 檢核輸入" in shown["final_diagram"]
    assert "底座 · 建立文件索引" in shown["final_diagram"]
    assert "底座 · 檢核輸出" in shown["final_diagram"]
    assert "底座 · 整理交付結果" in shown["final_diagram"]
    assert "rk_output -->|符合交付條件| rk_delivery" in shown["final_diagram"]
    assert shown["base_steps"]["version"] == 1
    assert shown["base_steps"]["boundaries"]["base.delivery.prepare"]["outputs"]["archive"] == "string"
    assert "機器檢核" in shown["final_diagram"]
    assert shown["tools"]["upper"]["parameters"]["temperature"] == 0.2
    assert shown["glossary"] == review["glossary"]
    assert shown["variables"]["parameter/upper/temperature"]["research"] == record
    assert "取樣溫度" in final["review_sections"]["implementation"]["display_text"]
    assert "若全數同意，回覆「確認定稿並匯出」" in final["display_text"]
    with pytest.raises(FlowError, match="所有預填內容"):
        approve(tmp_path, final, shown_sections=None, request_id="without-sections")
    with pytest.raises(FlowError, match="所有預填內容"):
        approve(tmp_path, final, shown_sections=section_hashes(draft), request_id="draft-sections")
    assert finish(tmp_path, draft, review) == final
    with pytest.raises(FlowError, match="定稿指紋"):
        approve(tmp_path, final, review_hash="another", request_id="wrong-design")
    with pytest.raises(FlowError, match="拒絕"):
        approve(tmp_path, final, user_text="先不要匯出", request_id="negative-reply")
    with pytest.raises(FlowError, match="明確核准"):
        approve(tmp_path, final, user_text="我還在看", request_id="pending-reply")
    with pytest.raises(FlowError, match="明確核准"):
        approve(tmp_path, final, user_text="初稿可進入定稿", request_id="draft-reply-is-not-export")
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
    resumed = studio_request(tmp_path, "get", {"project_id": draft["project_id"]})
    assert resumed["blueprint_proposal"]["build_preflight"] == final["build_preflight"]
    assert "請照常校閱和匯出已確認的藍圖" in resumed["blueprint_proposal"]["display_text"]
    exported = approve(tmp_path, final)
    service = WorkflowService(tmp_path)
    key = service.install_workflow(archive=exported["path"])["draft_id"]
    assert service.resolve_blueprint_capabilities(key)["status"] == "blocked"


def test_step_contracts_can_be_confirmed_before_host_tool_binding(
    tmp_path, workflow_design_review, desktop_test_catalog
):
    graph = data_graph()
    graph["nodes"][1].update(capability="test-sink", side_effect="external", permissions=["send"])
    graph["quality_contract"]["success_criteria"] = ["送交文字並取得回執"]
    graph["quality_contract"]["golden_cases"][0]["replies"] = [
        {"step": "copy", "kind": "authorize_external"},
        {"step": "copy", "kind": "external", "outputs": {"result": "ok"}},
    ]
    catalog = json.loads(desktop_test_catalog.read_bytes())
    tool = catalog["tools"]["test-sink"]
    port = {"type": "string", "required": True}
    tool.update(inputs={"source": port}, outputs={"result": port}, available=False)
    desktop_test_catalog.write_text(json.dumps(catalog))

    draft = sketch(tmp_path, graph)
    assert draft["build_preflight"]["status"] == "blocked"
    assert not draft["build_preflight"]["blocks_blueprint_export"]
    checklist = json.loads(draft["review_sections"]["process"]["display_text"])["逐步輸入輸出合約"]
    assert checklist["copy"]["輸入"]["source"] == {
        "source": "input://text", "port": {**port, "format": None, "items": None},
    }
    assert checklist["copy"]["輸出"]["result"]["用途"] == ["工作流交付 / original", "upper / source"]
    review = workflow_design_review(draft)
    review["tool_locations"]["copy"] = "deferred"
    final = finish(tmp_path, draft, review)
    assert final["design_review"]["tools"]["copy"]["location_deferred"]
    checklist = json.loads(final["review_sections"]["process"]["display_text"])["逐步輸入輸出合約"]
    assert checklist["copy"]["完成條件"] == review["step_decisions"]["copy"]["completion"]
    exported = approve(tmp_path, final)
    service = WorkflowService(tmp_path)
    key = service.install_workflow(archive=exported["path"])["draft_id"]
    blocked = service.resolve_blueprint_capabilities(key)
    assert blocked["build_status"] == "blocked"
    with pytest.raises(FlowError, match="草稿已保存"):
        service.install_workflow(draft_id=key, resolution={"capability_fingerprint": blocked["fingerprint"]})
    assert not service.workspace.discover(read_only=True)

    tool["available"] = True
    desktop_test_catalog.write_text(json.dumps(catalog))
    ready = service.resolve_blueprint_capabilities(key)
    assert ready["build_status"] == "ready"
    assert ready["bindings"]["copy"]["tool"] == "test-sink"
    verified = service.install_workflow(
        draft_id=key, resolution={"capability_fingerprint": ready["fingerprint"]}
    )
    assert verified["status"] == "verified" and verified["evidence"]["provider_calls"] == 0
    assert service.install_workflow(
        draft_id=key, build_id=verified["build_id"], install_hash=verified["install_hash"]
    )["status"] == "installed"


def test_deferred_decision_survives_prefill_and_requires_a_current_user_reply(
    tmp_path, workflow_design_review,
):
    graph = data_graph()
    graph["quality_contract"]["success_criteria"] = ["保留原始文字"]
    draft = sketch(tmp_path, graph)
    pid = draft["project_id"]

    def studio(operation, **params):
        return studio_request(tmp_path, operation, {"project_id": pid, **params})

    question = studio("next", domain="rules")
    assert question["suggested_answer"] == "保持來源內容"
    deferred = studio(
        "reply", expected_revision=draft["revision"], request_id="defer", domain="rules",
        question_id=question["question_id"], user_text="業務規則先留待討論。", response_kind="defer",
    )
    assert deferred["coverage"]["rules"]["user_text"] == "業務規則先留待討論。"
    assert studio("get")["design_progress"]["stage"] == "draft_stale"
    with pytest.raises(FlowError, match="已有更新"):
        finish(tmp_path, draft, workflow_design_review(draft))
    with pytest.raises(FlowError, match="新的實際回覆"):
        studio(
            "answer", expected_revision=deferred["revision"], request_id="guess", domain="rules",
            value="保持來源內容", status="inferred", confidence=0.9, evidence_refs=["assistant"],
        )
    preview = {
        "graph": graph, "design": {"rules": "保持來源內容"},
        "expected_revision": deferred["revision"], "request_id": "new-draft",
    }
    second = studio("prepare_blueprint", **preview)
    assert second["open_design_domains"] == ["rules"]
    next_step = studio("next_batch")
    assert next_step["design_progress"]["stage"] == "decisions_required"
    assert [question["domain"] for question in next_step["questions"]] == ["rules"]
    with pytest.raises(FlowError, match="缺少決策.*rules"):
        finish(tmp_path, second, workflow_design_review(second))
    decided = studio(
        "reply", expected_revision=second["revision"], request_id="answer-rules", domain="rules",
        question_id=next_step["questions"][0]["question_id"], user_text="保持來源內容",
    )
    current = studio("prepare_blueprint", **{
        **preview, "expected_revision": decided["revision"], "request_id": "reviewed-draft",
    })
    assert current["open_design_domains"] == []
    assert finish(tmp_path, current, workflow_design_review(current))["draft_id"] == current["proposal_id"]
