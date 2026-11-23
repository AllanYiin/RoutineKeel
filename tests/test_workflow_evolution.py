import copy
import json

import pytest
from flow_base.runtime import FlowError
from test_graph_builder import data_graph, import_graph
from test_quality_feedback import decision


def test_holdout_compares_fixture_bytes_even_when_the_names_differ(tmp_path):
    from flow_base.evolution.comparison import case_content

    folder = tmp_path / "fixtures"
    folder.mkdir()
    (folder / "training.txt").write_bytes(b"same input")
    (folder / "blind.txt").write_bytes(b"same input")

    def identity(name):
        return case_content(tmp_path, {"sources": [{"$file": "fixtures/" + name}]})

    assert identity("training.txt") == identity("blind.txt")
    (folder / "blind.txt").write_bytes(b"unseen input")
    assert identity("training.txt") != identity("blind.txt")


def test_approved_profile_supplies_input_and_reuses_only_identical_pure_work(tmp_path):
    from flow_base.graph.session import GraphSession
    from flow_base.quality import save_profile
    from flow_base.runtime import read_json
    from test_graph_scheduler import diamond, setup

    graph = diamond()
    graph["quality_contract"]["personalization"] = {
        "parameters": {"cache_pure_tasks": {"type": "boolean"}, "text": {"type": "string"}}
    }
    runtime, plan, bindings = setup(tmp_path, graph)
    runtime.initialize()
    save_profile(
        runtime,
        plan["graph"],
        "local",
        0,
        {"cache_pure_tasks": True, "text": "default"},
        "approved",
        "同意設定",
    )
    view = GraphSession.begin(
        runtime,
        plan,
        {"inputs": {}, "profile_id": "local", "profile_revision": 1},
        tmp_path,
        bindings,
    )
    state = read_json(GraphSession(runtime, view["session_id"]).path)
    assert view["outputs"] == {"result": "DEFAULT"}
    assert any(h["evidence"]["kind"] == "local_cache" for h in state["history"])
    override = GraphSession.begin(
        runtime,
        plan,
        {"inputs": {"text": "override"}, "profile_id": "local", "profile_revision": 1},
        tmp_path,
        bindings,
    )
    assert override["outputs"] == {"result": "OVERRIDE"}


def case(text):
    return {
        "inputs": {"text": text},
        "expected": {"original": text, "upper": text.upper()},
    }


def test_failed_local_comparison_retains_observed_usage(tmp_path):
    from flow_base.evolution.comparison import replay

    graph = data_graph()
    graph["quality_contract"]["golden_cases"][0]["expected"]["upper"] = "wrong"
    svc, key, _ = import_graph(tmp_path, graph)
    denied = replay(svc, key, graph["quality_contract"]["golden_cases"])
    assert not denied["passed"] and denied["observed_case_count"] == 1
    assert denied["measurement"] == "local_trial" and denied["cost_micro_usd"] == 0


def test_compare_fixed_defect_and_holdout_before_switch_and_rollback(
    tmp_path, monkeypatch, workflow_design_review,
):
    base = data_graph()
    base["nodes"][0]["capability"] = "text.copy"
    base["quality_contract"].update(golden_cases=[case("OK")], success_criteria=["交付原文與大寫"])
    svc, key, resolution = import_graph(tmp_path, base)
    build = svc.install_workflow(draft_id=key, resolution=resolution)
    svc.install_workflow(
        draft_id=key, build_id=build["build_id"], install_hash=build["install_hash"]
    )
    source = tmp_path / "job.json"
    source.write_text(json.dumps({"inputs": {"text": "bad"}}))
    result = svc.run_workflow_sync({"task": "graph_copy", "source": source})["blueprint"]
    assert result["outputs"]["upper"] == "bad"
    sid = result["session_id"]
    event, _ = decision(svc, sid, "correct", outputs=case("bad")["expected"])
    proposal = svc.workflow_evolution(
        sid,
        "propose",
        {
            "feedback_ids": [event["feedback_id"]],
            "category": "logic",
            "hypothesis": "大寫節點使用了複製",
            "impact": "交付大寫",
            "risk": "既有輸出可能改變",
            "request_id": "propose",
        },
    )

    def act(state, action, req, **params):
        return svc.workflow_evolution(
            sid,
            action,
            dict(
                proposal_id=state["proposal_id"],
                expected_revision=state["revision"],
                request_id=req,
                **params,
            ),
        )

    candidate = copy.deepcopy(base)
    other = svc.workflow_evolution(
        sid,
        "propose",
        {
            "feedback_ids": [event["feedback_id"]],
            "category": "goal",
            "hypothesis": "另一個目標",
            "impact": "調整成品",
            "risk": "舊結果會不同",
            "request_id": "other",
        },
    )
    rejected = act(other, "reject", "decline", user_text="維持原目標")
    assert rejected["phase"] == "rejected"
    candidate["workflow"].update(version="1.1.0", revision=2)
    candidate["nodes"][0]["capability"] = "text.upper"
    changed_goal = copy.deepcopy(candidate)
    changed_goal["workflow"]["goal"] = "其他目標"
    with pytest.raises(FlowError, match="目標"):
        act(
            proposal,
            "prepare",
            "changed-goal",
            graph=changed_goal,
            design={"rules": "保留原文"},
        )
    preview = act(proposal, "prepare", "design", graph=candidate, design={"rules": "保留原文"})
    preview = act(preview, "finalize", "finalize", design_id=preview["design_id"], review=workflow_design_review(preview))
    exported = act(
        preview,
        "export",
        "export",
        design_id=preview["design_id"],
        review_hash=preview["review_hash"],
        user_text="確認定稿並匯出",
    )
    held = tmp_path / "holdout.json"
    params = {
        "holdout_file": str(held),
        "holdout_origin": "獨立驗收資料",
        "unseen_by_proposer": True,
        "defect_cases": [{**case("bad"), "feedback_id": event["feedback_id"]}],
        "limits": {"review_count": 0, "cost_micro_usd": 0, "model_calls": 0},
    }
    held.write_text(json.dumps([case("bad")]))
    with pytest.raises(FlowError, match="重疊"):
        act(exported, "compare", "overlap", **params)
    held.write_text(
        json.dumps([{**case("third"), "expected": {"original": "third", "upper": "WRONG"}}])
    )
    failed = act(exported, "compare", "negative", **params)
    assert failed["phase"] == "comparison_failed"
    assert failed["comparison"]["candidate"]["observed_case_count"] == 3
    assert failed["comparison"]["candidate"]["cost_micro_usd"] == 0
    with pytest.raises(FlowError):
        act(
            failed,
            "activate",
            "model-score",
            comparison_hash=failed["comparison_hash"],
            user_text="高分",
        )
    held.write_text(json.dumps([case("fresh")]))
    compared = act(failed, "compare", "positive", **params)
    report = compared["comparison"]
    assert compared["phase"] == "compared" and report["base"]["fixed"]["passed"]
    assert not report["base"]["defects"]["passed"] and not report["base"]["holdout"]["passed"]
    assert report["candidate"]["case_count"] == 3
    assert svc.describe_workflow("graph_copy")["version"] == "1.0.0"
    with pytest.raises(FlowError, match="對話確認"):
        svc.update_workflow(report["candidate"]["archive"], plan_hash=report["update_plan"])
    from flow_base.evolution import promotion

    original_append = promotion.append

    def interrupted(folder, state, phase):
        if phase == "active":
            raise InterruptedError("中斷於套件切換與記錄提案之間")
        return original_append(folder, state, phase)

    with monkeypatch.context() as fault:
        fault.setattr(promotion, "append", interrupted)
        with pytest.raises(InterruptedError):
            act(
                compared,
                "activate",
                "approve",
                comparison_hash=compared["comparison_hash"],
                user_text="採用測過的新版",
            )
    active = act(
        compared,
        "activate",
        "approve",
        comparison_hash=compared["comparison_hash"],
        user_text="採用測過的新版",
    )
    assert active["phase"] == "active" and svc.describe_workflow("graph_copy")["version"] == "1.1.0"
    source.write_text(json.dumps({"inputs": {"text": "again"}}))
    assert (
        svc.run_workflow_sync(
            {"task": "graph_copy", "source": source, "output": tmp_path / "new-result"}
        )["blueprint"]["outputs"]["upper"]
        == "AGAIN"
    )
    restored = act(active, "rollback", "restore", user_text="退回前一版")
    assert (
        restored["phase"] == "rolled_back"
        and svc.describe_workflow("graph_copy")["version"] == "1.0.0"
    )
    assert (
        svc.run_workflow_sync(
            {"task": "graph_copy", "source": source, "output": tmp_path / "old-result"}
        )["blueprint"]["outputs"]["upper"]
        == "again"
    )
