"""Route a verified workflow revision through the existing upgrade acceptance path."""

from ..evolution import current, root
from ..runtime import FlowError, digest
from .source import require


def _evolution_state(service, work):
    source = service.install_workflow(draft_id=work["draft_id"])["source"]
    proposal_id = (
        source.get("graph", {})
        .get("provenance", {})
        .get("lineage", {})
        .get("improvement_id")
    )
    if proposal_id is None:
        return {}
    folder = root(service.workspace.runtime, proposal_id)
    if not folder.is_dir():
        return {}
    proposal = current(folder)
    if (
        proposal.get("candidate_key") != work["draft_id"]
        or proposal["workflow_id"] != work["workflow_id"]
    ):
        return {}
    return proposal


def update_details(service, work, plan):
    proposal = _evolution_state(service, work)
    if proposal:
        comparison = proposal.get("comparison", {})
        action = (
            "activate_update"
            if proposal["phase"] in {"compared", "active", "activating"}
            else "compare_update"
        )
        body = {
            "action": action,
            "proposal_id": proposal["proposal_id"],
            "session_id": proposal["session_id"],
            "revision": proposal["revision"],
            "phase": proposal["phase"],
            "comparison_hash": proposal.get("comparison_hash"),
            "comparison": comparison,
        }
    elif work["blueprint_schema_version"] == 1:
        body = {"action": "activate_update", "plan_hash": plan["plan_hash"]}
    else:
        body = {
            "action": "prepare_evolution",
            "reason": "請從正式工作流的執行回饋建立修訂提案，並匯出該提案關聯的候選藍圖。",
        }
    return {**body, "scope_hash": digest([work["draft_id"], plan["plan_hash"], body])}


def submit_update(service, work, payload):
    require(
        work["status"] == "needs_update" and work["intent"] == "install",
        "這項工作尚無待處理的更新。",
    )
    plan = work["update_plan"]
    details = update_details(service, work, plan)
    if (
        details["scope_hash"] != payload.get("scope_hash")
        or details["action"] != payload["action"]
    ):
        raise FlowError("REVISION_CONFLICT", "更新範圍已變更，請重新查詢。")
    if details["action"] == "compare_update":
        comparison = payload.get("comparison")
        require(isinstance(comparison, dict), "請提供回歸缺陷案例與獨立未見資料。")
        service.workflow_evolution(
            details["session_id"],
            "compare",
            {
                **comparison,
                "proposal_id": details["proposal_id"],
                "expected_revision": details["revision"],
                "request_id": payload["request_id"],
                "completion_work_id": work["work_id"],
            },
        )
        return {**work, "update": update_details(service, work, plan)}

    answer = payload.get("user_text")
    require(
        isinstance(answer, str) and 0 < len(answer.strip()) <= 4000,
        "請提交這次升版的實際核准。",
    )
    if details.get("proposal_id"):
        if details["phase"] == "active":
            result = _evolution_state(service, work)
        else:
            result = service.workflow_evolution(
                details["session_id"],
                "activate",
                {
                    "proposal_id": details["proposal_id"],
                    "expected_revision": details["revision"],
                    "request_id": payload["request_id"],
                    "user_text": answer,
                    "comparison_hash": details["comparison_hash"],
                },
            )
        install = {
            "status": "updated",
            "update_id": result["update_id"],
            "workflow_id": result["workflow_id"],
        }
        return {
            **work,
            "status": "installed",
            "installation": install,
            "update": update_details(service, work, plan),
            "update_confirmation": answer,
        }

    candidate = work["candidate"]
    from .installer import checked_candidate, draft_dir

    folder = (
        draft_dir(service.workspace, work["draft_id"])
        / "builds"
        / candidate["build_id"]
    )
    checked_candidate(service.workspace, folder, candidate["install_hash"])
    install = service.update_workflow(
        candidate["archive"], plan_hash=plan["plan_hash"], task=work["workflow_id"]
    )
    return {
        **work,
        "status": "installed",
        "installation": install,
        "update_confirmation": answer,
    }
