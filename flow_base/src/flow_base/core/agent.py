"""Describe installed workflow data and present durable state to a conversational host."""

import json
import zipfile
from contextlib import closing

from ..outputs import reject_links
from ..runtime import FlowError, read_json, sha256


def action(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


def describe(task):
    m = task.manifest
    info = {
        "intent": m.description or m.title,
        "keywords": m.keywords,
        "inputs": {"extensions": m.input_extensions},
        "named_outputs": m.outputs,
        "required_tools": None,
        "human_gates": None,
        "resource_upper_bound": None,
        "next_actions": [action("plan_workflow", workflow_id=m.id)],
    }
    if not m.python_module.startswith("rk_bp_"):
        return info
    wheel = task.folder / m.package
    reject_links(wheel)
    try:
        if sha256(wheel) != m.sha256:
            raise ValueError("wheel fingerprint")
        with zipfile.ZipFile(wheel) as z:

            def metadata(name):
                member = z.getinfo(m.python_module + "/" + name)
                if member.file_size > 32 * 1024**2:
                    raise ValueError("oversized metadata")
                return json.loads(z.read(member))

            plan = metadata("plan.json")
            if plan["format"] != 2:
                info["named_outputs"] = plan["final_outputs"]
                return info
            bindings = metadata("tool-signatures.json")["bindings"]
        graph = plan["graph"]
        tools = [
            {
                "node_id": name,
                **{
                    field: b.get(field)
                    for field in (
                        "tool",
                        "version",
                        "provider",
                        "permissions",
                        "side_effect",
                        "resources",
                    )
                },
            }
            for name, b in bindings.items()
        ]
        gates = []

        def collect(graph, prefix):
            for n in graph["nodes"]:
                node_id = prefix + n["id"]
                if n["kind"] == "human_review":
                    gates.append({"node_id": node_id, "reason": "human_review"})
                if (
                    n.get("on_limit") == "review_required"
                    or n.get("on_item_failure") == "review_required"
                    or n.get("on_error") == "review_required"
                ):
                    gates.append({"node_id": node_id, "reason": "exception_review"})
                if n.get("body"):
                    collect(n["body"], node_id + "/")

        collect(graph, "")
        for tool in tools:
            if tool["provider"] == "desktop_host" and (
                tool["permissions"]
                or tool["side_effect"] == "external"
                or (tool["resources"] or {}).get("cost_micro_usd", 0) > 0
            ):
                gates.append({"node_id": tool["node_id"], "reason": "authorize_external"})
        if graph["quality_contract"].get("review_policy", {}).get("required"):
            gates.append({"reason": "result_review"})
        from ..graph.iterations import upper_cost

        try:
            budget = upper_cost(plan, bindings)
        except FlowError:
            budget = None
        return {
            **info,
            "intent": graph["workflow"]["goal"],
            "inputs": graph["inputs"],
            "named_outputs": graph["outputs"],
            "required_tools": tools,
            "human_gates": gates,
            "resource_upper_bound": budget,
            "success_criteria": graph["quality_contract"].get("success_criteria", []),
            "graph_hash": plan["graph_hash"],
        }
    except (OSError, ValueError, TypeError, KeyError, zipfile.BadZipFile) as exc:
        raise FlowError("TASK_ARCHIVE", "無法核對已安裝工作流的目錄資料") from exc


def from_run(runtime, run_id):
    if not run_id:
        return None
    sid = "job_" + run_id.removeprefix("run_")
    if (runtime.home / "blueprint_jobs" / sid / "state.json").is_file():
        return sid
    # A resumed job has a new run ID. Its verified delivery records the original job ID.
    from .lifecycle import resolve_manifest_reference

    with closing(runtime.connect()) as db:
        row = db.execute(
            """SELECT a.manifest_json,a.manifest_sha256 FROM deliveries d
            JOIN artifact_sets a ON a.artifact_set_id=d.artifact_set_id
            WHERE d.run_id=? AND d.status='published' LIMIT 1""",
            (run_id,),
        ).fetchone()
    if row and row[0]:
        manifest = resolve_manifest_reference(runtime, row[0])
        if not manifest.is_file() or sha256(manifest) != row[1]:
            return None
        path = manifest.parent / "report.json"
        if path.is_file():
            report = read_json(path)
            if report.get("run_id") == run_id:
                return report.get("blueprint", {}).get("session_id")
    return None


def session_status(service, sid):
    view = service.blueprint_task(sid, "status")
    pending = view.get("pending_tasks") or ([view["pending"]] if view.get("pending") else [])
    next_actions = []
    for task in pending:
        op = (
            "reconcile"
            if task.get("claimed")
            else "authorize"
            if task["kind"] == "authorize_external"
            else "claim"
            if task.get("claim_required")
            else "complete"
        )
        payload = {
            "revision": view["revision"],
            "task_id": task["id"],
            **{k: task[k] for k in ("scope_hash", "payload_hash") if k in task},
        }
        next_actions.append(action("blueprint_task", session_id=sid, operation=op, payload=payload))
        if task["kind"] == "authorize_external":
            next_actions.append(
                action("blueprint_task", session_id=sid, operation="decline", payload=payload)
            )
    if view.get("assessment_id"):
        next_actions.append(action("workflow_quality", session_id=sid, operation="preview"))
    return {
        "session_id": sid,
        "workflow_id": view["workflow_id"],
        "session_status": view["status"],
        "event_source": "workflow_execution",
        "display_text": (
            "、".join(p.get("prompt") or p["step"] for p in pending)
            if pending
            else {
                "complete": "流程已完成",
                "failed": "流程未完成，請查看錯誤",
                "cancelled": "流程已取消",
            }.get(view["status"], "工作執行中")
        ),
        "result_status": view.get("result_status"),
        "delivery_status": view.get("delivery_status"),
        "revision": view["revision"],
        "pending": [{"node_id": p["step"], **p} for p in pending],
        "outputs": view.get("outputs"),
        "next_actions": next_actions,
    }


def conversation(service, *, execution_id=None, session_id=None, proposal_id=None):
    if proposal_id:
        if not session_id or execution_id:
            raise FlowError("INVALID_CONFIGURATION", "查詢提案需要原工作識別碼")
        p = service.workflow_evolution(session_id, "get", {"proposal_id": proposal_id})
        options = {
            "proposed": ["prepare", "reject"],
            "prepared": ["export", "prepare", "reject"],
            "profile_candidate": ["activate", "reject"],
            "exported": ["compare", "reject"],
            "compared": ["activate", "compare", "reject"],
            "comparison_failed": ["prepare", "compare", "reject"],
            "active": ["rollback"],
        }
        return {
            "session_id": session_id,
            "proposal_id": proposal_id,
            "phase": p["phase"],
            "hypothesis": p["hypothesis"],
            "expected_impact": p["expected_impact"],
            "risk": p["risk"],
            "comparison_hash": p.get("comparison_hash"),
            "comparison": p.get("comparison"),
            "next_actions": [
                action(
                    "workflow_evolution",
                    session_id=session_id,
                    operation=o,
                    payload={
                        "proposal_id": proposal_id,
                        "expected_revision": p["revision"],
                        **(
                            {"comparison_hash": p["comparison_hash"]}
                            if o == "activate" and "comparison_hash" in p
                            else {}
                        ),
                    },
                )
                for o in options.get(p["phase"], [])
            ],
        }
    if session_id and not execution_id:
        return session_status(service, session_id)
    if execution_id and not session_id:
        s = service.get_workflow_status(execution_id)
        sid = from_run(service.workspace.runtime, s["run_id"])
        result = {
            **s,
            "event_source": "workflow_execution",
            "next_actions": [action("get_workflow_status", execution_id=execution_id)],
        }
        if sid:
            result.update(session_status(service, sid))
            if result["result_status"] is None:
                result["result_status"] = s["result_status"]
            if result["delivery_status"] is None:
                result["delivery_status"] = s["delivery_status"]
        if s["status"] == "succeeded":
            result["next_actions"].append(action("get_workflow_result", execution_id=execution_id))
        elif s["status"] in {"failed", "interrupted", "cancelled"}:
            result["next_actions"] = []
        return result
    if not session_id and not execution_id:
        return {
            "workflows": service.list_workflows(),
            "next_actions": [action("studio_create_project"), action("list_workflows")],
        }
    raise FlowError("INVALID_CONFIGURATION", "請指定一次執行或一個工作")
