"""Detect a running Graph's authority drift and retain one scoped repair work item."""

from ..blueprints import installer
from ..blueprints.completion import completion
from ..core.service import WorkflowService
from ..runtime import FlowError, digest
from .installed import installed_graph


def _binding_scope(binding, *, new_lock):
    if binding is None or not new_lock:
        return binding
    return {key: value for key, value in binding.items() if key != "lock_hash"}


def _affected(session, state, live, *, new_lock=False):
    changed = sorted(
        key
        for key, binding in state["bindings"].items()
        if _binding_scope(live["bindings"].get(key), new_lock=new_lock)
        != _binding_scope(binding, new_lock=new_lock)
    )
    impacted = {key.split("/", 1)[0] for key in changed}
    dependencies = session.dependencies(state)
    for key in state["plan"]["order"]:
        if impacted.intersection(dependencies[key]):
            impacted.add(key)
    return changed, sorted(impacted)


def detect_rebind(session, state, live):
    """Return affected root nodes. Only an installed Run can create repair work."""
    previous = state.get("rebind") or {}
    if live["fingerprint"] == state["plan"]["capability_fingerprint"]:
        if previous.get("status") == "needed":
            state["rebind"] = {**previous, "status": "restored"}
            session.checkpoint(state, None, "binding_restored", work_id=previous.get("work_id"))
        return set()
    if not state.get("run_id") or state.get("owner_session"):
        raise FlowError("INVALID_CONFIGURATION", "能力或權限已變更")
    changed, affected = _affected(session, state, live)
    if not changed:
        raise FlowError("BINDING_STALE", "能力觀測與原計畫不同。")
    pending = sorted(
        task["idempotency_key"]
        for task in state.get("pending_tasks", {}).values()
        if task.get("claimed") and task.get("idempotency_key")
    )
    impact = {
        "status": "needed",
        "changed_bindings": changed,
        "affected": affected,
        "pending_operations": pending,
        "work_id": previous.get("work_id"),
    }
    if impact != previous:
        state["rebind"] = impact
        session.checkpoint(
            state, None, "rebind_detected", affected=affected, pending_operations=pending
        )
    if impact["work_id"] is None and not pending:
        service = WorkflowService(session.runtime.workspace_root)
        origin = {
            "kind": "runtime_rebind",
            "session_id": session.id,
            "source_run_id": state["run_id"],
            "plan_hash": state["plan_hash"],
            "bindings_hash": state["plan"]["bindings_hash"],
        }
        try:
            plan = state["plan"]
            source = installed_graph(
                service.workspace.root, f"{plan['workflow']}@{plan['version']}", include_source=True
            )
            if source["blueprint_sha256"] != plan["source_hash"]:
                raise FlowError("BINDING_STALE", "目前安裝的套件沒有原 Run 的藍圖。")
            draft = installer.recover_embedded_draft(
                service.workspace, source["source"], plan["source_hash"]
            )
            work = completion(
                service,
                "start",
                payload={
                    "request_id": "runtime-rebind-" + digest(origin),
                    "draft_id": draft,
                    "intent": "build",
                    "offline": state.get("offline", False),
                },
                origin=origin,
            )
        except (FlowError, OSError, ValueError) as exc:
            state["rebind"] = {**impact, "repair_error": getattr(exc, "code", "BINDING_STALE")}
            session.checkpoint(
                state, None, "rebind_source_unavailable", code=state["rebind"]["repair_error"]
            )
        else:
            state["rebind"] = {**impact, "work_id": work["work_id"]}
            session.checkpoint(state, None, "rebind_work_created", work_id=work["work_id"])
    return set(affected)
