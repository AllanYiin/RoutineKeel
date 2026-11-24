"""Bounded, sequential subgraphs. A child job ID survives a crash at either checkpoint."""

from time import time as now

from ..blueprints.sessions import refs
from ..blueprints.source import require
from ..runtime import digest, read_json
from .controls import predicate_value


def upper_cost(plan, bindings):
    estimate = {"work": len(plan["graph"]["nodes"]), "model_calls": 0, "cost_micro_usd": 0}
    for node in plan["graph"]["nodes"]:
        if node["kind"] in {"foreach", "repeat_until"}:
            for field in estimate:
                estimate[field] += node["limits"]["max_" + field]
        elif node["kind"] == "task":
            descriptor = bindings[node["id"]]
            resources = descriptor.get("resources") or {}
            if descriptor["provider"] == "toolchain":
                estimate["work"] += resources["work"]
            for field in ("model_calls", "cost_micro_usd"):
                cap = resources.get(field)
                require(type(cap) is int and cap >= 0, "迭代前須有可核對的工具資源上限：" + field)
                estimate[field] += cap
    return estimate


def merged_meter(nodes):
    actual = {"work": 0, "model_calls": 0, "cost_micro_usd": 0}
    for record in nodes.values():
        actual["work"] += record["attempt"]
        for field, value in record.get("usage", {}).items():
            if field in {"model_calls", "cost_micro_usd"}:
                actual[field] += value
        for container in ("loop", "chain"):
            for field, value in record.get(container, {}).get("spent", {}).items():
                actual[field] += value
    return actual


def bounded_failure(session, state, node, reason):
    record = state["nodes"][node["id"]]
    policy = node.get("on_limit", node.get("on_item_failure"))
    strict = state["plan"]["graph"]["quality_contract"].get("execution_review", {}).get("mode") == "stop_on_error"
    if policy == "review_required" and not strict and reason != "WORKFLOW_CHECK_FAILED":
        session.wait_for(
            state,
            node,
            "iteration_review",
            {
                "reason": reason,
                "last": record["loop"].get("value"),
                "completed_items": len(
                    [i for i in record["loop"]["items"] if i["status"] == "complete"]
                ),
            },
        )
    else:
        record.update(status="failed", error=reason)
        session.checkpoint(state, node["id"], "iteration_stopped", reason=reason)


def mirror(session, state, node, entry, view):
    key = node["id"]
    for child_task in view["pending_tasks"]:
        task_id = digest([session.id, entry["id"], child_task["id"]])
        state["pending_tasks"][task_id] = {
            **child_task,
            "id": task_id,
            "step": key,
            "child_step": child_task["step"],
            "iteration_id": entry["id"],
            "child_id": entry["child_session"],
            "child_task_id": child_task["id"],
            "child_revision": view["revision"],
        }
    state["nodes"][key]["status"] = "waiting"
    session.checkpoint(state, key, "iteration_waiting", iteration_id=entry["id"])


def run_loop(session, state, node):
    from .session import GraphSession

    key = node["id"]
    record = state["nodes"][key]
    each = node["kind"] == "foreach"
    initial = session.value(state, node["items"] if each else node["initial"]["source"])
    child = state["plan"]["children"][key]
    prefix = key + "/"
    bindings = {n[len(prefix) :]: v for n, v in state["bindings"].items() if n.startswith(prefix)}
    limits = node["limits"]
    loop = record.setdefault(
        "loop",
        {
            "epoch": record["attempt"],
            "started": now(),
            "index": 0,
            "items": [],
            "inputs_hash": digest(initial),
            "value": None if each else initial,
            "spent": {"work": 0, "model_calls": 0, "cost_micro_usd": 0},
        },
    )
    require(loop["inputs_hash"] == digest(initial), "迭代輸入與檢查點不符")
    count = len(initial) if each else node["max_iterations"]
    if each and count > node["max_items"]:
        return bounded_failure(session, state, node, "MAX_ITEMS")
    if (
        not each
        and loop["index"] == 0
        and not loop["items"]
        and node["condition"]["source"] == node["body"]["outputs"]["value"]
        and predicate_value(node["condition"], initial)
    ):
        return session.complete_node(
            state, node, {"value": initial}, {"kind": "iteration", "count": 0}
        )
    while loop["index"] < count and not loop.get("satisfied"):
        if session.cancellation(state):
            return
        index = loop["index"]
        if now() - loop["started"] > limits["timeout_seconds"]:
            return bounded_failure(session, state, node, "TIME_LIMIT")
        value = initial[index] if each else loop["value"]
        if len(loop["items"]) == index:
            reserve = upper_cost(child, bindings)
            if any(loop["spent"][f] + n > limits["max_" + f] for f, n in reserve.items()):
                return bounded_failure(session, state, node, "RESOURCE_LIMIT")
            identity = digest([session.id, key, loop["epoch"], index, digest(value)])
            entry = {
                "id": identity,
                "index": index,
                "input_hash": digest(value),
                "child_session": "job_" + identity[:32],
                "status": "started",
                "reserved": reserve,
            }
            loop["items"].append(entry)
            session.checkpoint(state, key, "iteration_started", iteration_id=identity, index=index)
        entry = loop["items"][index]
        child_session = GraphSession(session.runtime, entry["child_session"])
        if child_session.path.exists():
            snapshot = read_json(child_session.path)
            require(
                snapshot["plan_hash"] == digest(child)
                and snapshot.get("owner_session") == session.id,
                "子工作版本不符",
            )
            view = child_session.interact("next", {})
            if record.pop("retry_child", False) and view["status"] == "failed":
                reserve = upper_cost(child, bindings)
                if any(loop["spent"][f] + n > limits["max_" + f] for f, n in reserve.items()):
                    return bounded_failure(session, state, node, "RESOURCE_LIMIT")
                failed = [k for k, r in view["nodes"].items() if r["status"] == "failed"]
                require(len(failed) == 1, "請先檢視子圖中的多個失敗原因")
                view = child_session.interact(
                    "retry",
                    {
                        "revision": view["revision"],
                        "step": failed[0],
                        "reason": "重新處理本項目的失敗節點",
                        "request_id": digest([entry["id"], view["revision"], "retry"]),
                    },
                )
        else:
            request = {"inputs": {"item" if each else "value": value}}
            view = GraphSession.begin(
                session.runtime,
                child,
                request,
                session.runtime.workspace_root,
                bindings,
                key=entry["id"][:32],
                owner_session=session.id,
                known_assets=set(refs(value)),
                offline=state.get("offline", False),
            )
        if view["pending_tasks"]:
            return mirror(session, state, node, entry, view)
        successful = view["status"] == "complete"
        if "execution_review" in child["graph"]["quality_contract"]:
            from ..quality import recorded_step_checks

            entry["output_checks"] = [
                {**check, "iteration_id": check.get("iteration_id", entry["id"])}
                for check in recorded_step_checks(view["nodes"])
            ]
        measured = merged_meter(view["nodes"])
        previous = entry.get("usage", {})
        entry.update(
            status="complete" if successful else "failed",
            usage=measured,
            has_external_claim=any(
                r.get("external_claim") or r.get("loop", {}).get("external_claim")
                for r in view["nodes"].values()
            ),
        )
        if entry["has_external_claim"]:
            loop["external_claim"] = True
        for f, n in measured.items():
            loop["spent"][f] += n - previous.get(f, 0)
        if now() - loop["started"] > limits["timeout_seconds"]:
            return bounded_failure(session, state, node, "TIME_LIMIT")
        if any(loop["spent"][f] > limits["max_" + f] for f in measured):
            return bounded_failure(session, state, node, "RESOURCE_LIMIT")
        strict = child["graph"]["quality_contract"].get("execution_review", {}).get("mode") == "stop_on_error"
        if not successful and "execution_review" in child["graph"]["quality_contract"] and any(
            step.get("error") in {"WORKFLOW_CHECK_FAILED", "REVIEW_DECLINED"}
            for step in view["nodes"].values()
        ):
            return bounded_failure(session, state, node, "WORKFLOW_CHECK_FAILED")
        if not successful and (strict or not each or node["on_item_failure"] != "collect_errors"):
            return bounded_failure(session, state, node, "CHILD_FAILED")
        if successful:
            entry["result"] = view["outputs"]["result" if each else "value"]
            entry["result_hash"] = digest(entry["result"])
            if not each:
                loop["value"] = entry["result"]
        loop["index"] += 1
        if not each and successful:
            outcome = next(
                name
                for name, ref in node["body"]["outputs"].items()
                if ref == node["condition"]["source"]
            )
            loop["satisfied"] = predicate_value(node["condition"], view["outputs"][outcome])
        session.checkpoint(state, key, "iteration_finished", iteration_id=entry["id"], index=index)
        if loop.get("satisfied"):
            break
    if each:
        values = (
            [
                {"item_id": i["id"], "status": i["status"], "value": i.get("result")}
                for i in loop["items"]
            ]
            if node["on_item_failure"] == "collect_errors"
            else [i["result"] for i in loop["items"]]
        )
        return session.complete_node(
            state, node, {"results": values}, {"kind": "foreach", "count": loop["index"]}
        )
    if loop.get("satisfied"):
        return session.complete_node(
            state, node, {"value": loop["value"]}, {"kind": "repeat_until", "count": loop["index"]}
        )
    return bounded_failure(session, state, node, "MAX_ITERATIONS")


def forward_reply(session, state, pending, operation, payload, request_id, stamp, simulation):
    from .session import GraphSession

    child = GraphSession(session.runtime, pending["child_id"])
    require(read_json(child.path).get("owner_session") == session.id, "子工作不屬於本次執行")
    key = pending["step"]
    node = next(n for n in state["plan"]["graph"]["nodes"] if n["id"] == key)
    if (
        operation in {"authorize", "claim"}
        and not pending.get("claimed")
        and "loop" in state["nodes"][key]
        and now() - state["nodes"][key]["loop"]["started"] > node["limits"]["timeout_seconds"]
    ):
        child.interact(
            "cancel", {"revision": pending["child_revision"], "request_id": "expired-" + request_id}
        )
        state["pending_tasks"] = {
            i: t for i, t in state["pending_tasks"].items() if t.get("child_id") != child.id
        }
        bounded_failure(session, state, node, "TIME_LIMIT")
        return session.view(state)
    response = child.interact(
        operation,
        {
            **payload,
            "task_id": pending["child_task_id"],
            "revision": pending["child_revision"],
            "request_id": request_id,
        },
        simulation=simulation,
    )
    if response.get("code") != "RECONCILE_REQUIRED" or response.get("dispatch"):
        state["requests"][request_id] = stamp
    key = pending["step"]
    state["pending_tasks"] = {
        k: v for k, v in state["pending_tasks"].items() if v.get("child_id") != child.id
    }
    state["nodes"][key]["status"] = "ready"
    session.checkpoint(state, key, "iteration_reply", iteration_id=pending["iteration_id"])
    result = session.advance(state)
    if response.get("dispatch"):
        result["dispatch"] = response["dispatch"]
    if response.get("event"):
        result["event"] = response["event"]
    return result
