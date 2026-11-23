"""Runtime evidence and isolated workflow regressions for Pattern proposals."""

import json
from contextlib import closing

from packaging.version import Version
from routinekeel_contracts.domain_assets import content_hash

from ..blueprints.source import require
from ..core.service import WorkflowService
from ..graph.installed import installed_graph
from ..graph.session import GraphSession
from ..runtime import FlowError, digest, read_json
from .comparison import replay, unique
from .proposals import require_active_feedback

_SIGNS = {
    "pattern_defect",
    "configuration",
    "profile_preference",
    "adapter_difference",
    "security",
    "missing_value",
    "wrong_total",
    "fact_drift",
    "timing",
}


def installed_dependents(service, pattern_ref):
    results = []
    for entry in service.workspace.discover(read_only=True).values():
        manifest = entry.manifest
        if manifest.python_module != f"rk_bp_{manifest.id}":
            continue
        workflow_ref = f"{manifest.id}@{manifest.version}"
        try:
            observed = installed_graph(service.workspace.root, workflow_ref)
        except FlowError as error:
            if error.code != "GRAPH_UNSUPPORTED":
                raise
            continue
        for pattern in observed["graph"]["provenance"].get("pattern_compositions", []):
            if pattern["ref"] == pattern_ref:
                row = {
                    "workflow_ref": workflow_ref,
                    "blueprint_hash": observed["blueprint_sha256"],
                    "wheel_hash": observed["wheel_sha256"],
                    "pattern_hash": pattern["hash"],
                }
                if row not in results:
                    results.append(row)
    return sorted(results, key=lambda row: row["workflow_ref"])


def observe(workspace, pattern_ref, feedback_ids):
    if not isinstance(feedback_ids, list) or not 1 <= len(feedback_ids) <= 50:
        raise FlowError("INVALID_CONFIGURATION", "請提供具名的實際品質回饋。")
    if any(
        not isinstance(value, str) or not value.startswith("feedback_") for value in feedback_ids
    ):
        raise FlowError("INVALID_CONFIGURATION", "回饋識別不符合契約。")
    service = WorkflowService(workspace)
    runtime = service.workspace.runtime
    require((runtime.home / "state.sqlite3").is_file(), "工作區沒有品質回饋。")
    evidence = []
    with closing(runtime.connect()) as db:
        for identifier in sorted(set(feedback_ids)):
            row = db.execute(
                """SELECT f.body AS feedback,a.body AS assessment,a.graph_hash
                FROM graph_feedback f JOIN graph_assessments a ON a.id=f.assessment_id
                WHERE f.id=?""",
                (identifier,),
            ).fetchone()
            require(row, "找不到已保存的品質回饋。")
            event = json.loads(row["feedback"])
            require(event["decision"] in {"correct", "reject"}, "需使用問題或修正回饋。")
            require_active_feedback(db, event)
            assessment = json.loads(row["assessment"])
            session = read_json(GraphSession(runtime, assessment["session_id"]).path)
            plan = session["plan"]
            graph = plan["graph"]
            require(
                plan["graph_hash"] == row["graph_hash"] == digest(graph)
                and digest(plan) == session["plan_hash"]
                and digest(event["before"]["outputs"]) == event["before_hash"]
                and digest(event["after"]["outputs"]) == event["after_hash"],
                "回饋成果或工作流指紋已變更。",
            )
            matches = [
                pattern
                for pattern in graph["provenance"].get("pattern_compositions", [])
                if pattern["ref"] == pattern_ref
            ]
            require(matches, "回饋來源未使用指定的 Pattern 版本。")
            matched = matches[0]
            evidence.append(
                {
                    "feedback_id": identifier,
                    "assessment_id": event["assessment_id"],
                    "workflow_ref": f"{plan['workflow']}@{plan['version']}",
                    "blueprint_hash": plan["source_hash"],
                    "pattern_hash": matched["hash"],
                    "binding_hash": plan["bindings_hash"],
                    "profile_hash": content_hash(session.get("profile")),
                    "before_hash": event["before_hash"],
                    "after_hash": event["after_hash"],
                    "scope": event["scope"],
                    "signs": sorted(set(event["tags"]) & _SIGNS),
                    "failed_checks": sorted(
                        entry["id"]
                        for entry in event["before"]["checks"]
                        if entry["status"] == "fail"
                    ),
                }
            )
    return {"attribution": evidence, "dependents": installed_dependents(service, pattern_ref)}


def _replay_summary(service, draft_id, cases):
    return {
        key: value
        for key, value in replay(service, draft_id, cases).items()
        if key not in {"archive", "reason"}
    }


def compare(workspace, state, candidate_drafts, negative_cases, limits):
    if not isinstance(candidate_drafts, dict) or not isinstance(negative_cases, dict):
        raise FlowError("INVALID_CONFIGURATION", "比較須提供新版草稿及各流程反例。")
    service = WorkflowService(workspace)
    feedback_ids = [row["feedback_id"] for row in state["attribution"]["observations"]]
    observe(workspace, state["source_ref"], feedback_ids)
    observed = installed_dependents(service, state["source_ref"])
    require(observed == state["dependents"], "依賴工作流已改變，須重新核對影響範圍。")
    expected = {item["workflow_ref"] for item in observed}
    require(
        candidate_drafts.keys() == negative_cases.keys() == expected,
        "每個受影響工作流都要有新版草稿及反例。",
    )
    require(
        isinstance(limits, dict)
        and limits.keys() <= {"cost_micro_usd", "model_calls", "review_count"}
        and all(type(value) is int and value >= 0 for value in limits.values()),
        "品質比較上限無效。",
    )
    results = []
    for dependent in observed:
        workflow = dependent["workflow_ref"]
        source_id = dependent["blueprint_hash"]
        candidate_id = candidate_drafts[workflow]
        source = service.install_workflow(draft_id=source_id)["source"]["graph"]
        candidate = service.install_workflow(draft_id=candidate_id)["source"]["graph"]
        require(
            candidate["workflow"]["id"] == source["workflow"]["id"]
            and Version(candidate["workflow"]["version"]) > Version(source["workflow"]["version"])
            and candidate["provenance"].get("pattern_rehearsal") == state["proposal_id"]
            and any(
                row["ref"] == state["target_ref"] and row["hash"] == state["target_hash"]
                for row in candidate["provenance"].get("pattern_compositions", [])
            ),
            "新版草稿須是此提案的明確試做版本。",
        )
        baseline = unique(source["quality_contract"]["golden_cases"])
        new_cases = unique(candidate["quality_contract"]["golden_cases"])
        negatives = unique(negative_cases[workflow])
        require(
            negatives and all(case in new_cases for case in baseline + negatives),
            "新版須涵蓋原 Golden Cases 及獨立反例。",
        )
        require(any(case not in baseline for case in negatives), "反例必須新增可驗證的案例。")
        before = _replay_summary(service, source_id, baseline)
        after = _replay_summary(service, candidate_id, new_cases)
        passed = (
            before["passed"]
            and after["passed"]
            and all(after[key] is not None and after[key] <= limit for key, limit in limits.items())
        )
        results.append(
            {
                "workflow_ref": workflow,
                "baseline": before,
                "candidate": after,
                "passed": passed,
                "old_blueprint_hash": source_id,
                "new_blueprint_hash": candidate_id,
                "baseline_case_hash": digest(baseline),
                "negative_case_hash": digest(negatives),
                "output_change": source["outputs"] != candidate["outputs"],
            }
        )
    require(
        installed_dependents(service, state["source_ref"]) == observed,
        "比較期間已安裝版本變更，請重新掃描。",
    )
    observe(workspace, state["source_ref"], feedback_ids)
    return {
        "status": "PASS" if results and all(row["passed"] for row in results) else "FAIL",
        "comparisons": results,
        "limits": limits,
    }
