"""Activate a verified replacement for one job without changing shared installations."""

import io
import json
import re
import zipfile
from contextlib import closing

from ..blueprints import installer
from ..blueprints.completion_store import CompletionStore
from ..blueprints.sessions import refs
from ..blueprints.source import require
from ..core.lifecycle import RunStore
from ..engine import reserve_workflow_run, run_workflow
from ..outputs import reject_links
from ..runtime import FlowError, atomic_bytes, atomic_json, digest, read_json
from ..workspace import Task
from .external import read_observation
from .session import GraphSession


def _unsettled(state):
    return any(task.get("claimed") for task in state.get("pending_tasks", {}).values())


def _frozen_state_hash(state):
    return digest(
        {
            key: value
            for key, value in state.items()
            if key not in {"audit", "handoff", "continuation"}
        }
    )


def _write_ticket(path, request):
    reject_links(path)
    if path.is_file():
        require(read_json(path) == request, "續做的執行憑據已變更。")
    else:
        atomic_json(path, request)


def _read_candidate(service, state):
    impact = state.get("rebind", {})
    require(
        impact.get("status") == "needed" and impact.get("work_id"),
        "此 Run 沒有待啟用的重綁工作。",
    )
    work = CompletionStore(service.workspace.runtime).read(impact["work_id"])
    require(
        work.get("status") == "verified"
        and work.get("origin", {}).get("source_run_id") == state["run_id"]
        and work["origin"].get("session_id") == state["session_id"]
        and work["origin"].get("plan_hash") == state["plan_hash"]
        and work["origin"].get("bindings_hash") == state["plan"]["bindings_hash"]
        and work["draft_id"] == state["plan"]["source_hash"],
        "補強工作或已驗證候選與原 Run 不符。",
    )
    candidate = work["candidate"]
    require(
        bool(re.fullmatch(r"[0-9a-f]{32}", candidate["build_id"])),
        "重綁候選識別不合法。",
    )
    dest = (
        installer.draft_dir(service.workspace, work["draft_id"]) / "builds" / candidate["build_id"]
    )
    reject_links(dest)
    record, manifest, files, modes, archive_hash, bindings = installer.checked_candidate(
        service.workspace,
        dest,
        candidate["install_hash"],
    )
    with zipfile.ZipFile(io.BytesIO(files[manifest.package])) as wheel:
        plan = json.loads(wheel.read(manifest.python_module + "/plan.json"))
    require(
        plan["workflow"] == state["plan"]["workflow"]
        and plan["version"] == state["plan"]["version"]
        and plan["graph"] == state["plan"]["graph"]
        and plan["source_hash"] == state["plan"]["source_hash"]
        and record["plan_hash"] == digest(plan)
        and digest(bindings) == plan["bindings_hash"],
        "重綁候選變更了原工作流的業務計畫或驗收範圍。",
    )
    return record, manifest, files, modes, archive_hash, bindings, plan


def _effects(binding):
    leaves = binding["chain_bindings"].values() if binding["provider"] == "toolchain" else [binding]
    destinations, permissions = set(), set()
    network = False
    for leaf in leaves:
        network = network or leaf.get("network", True)
        destination = leaf.get("destination")
        if not destination and (leaf.get("network", True) or leaf.get("side_effect") == "external"):
            destination = leaf["tool"]
        if destination:
            destinations.add(destination)
        permissions.update(leaf.get("permissions", []))
    resources = binding.get("resources") or {}
    cost = resources.get("cost_micro_usd", 0)
    batch = resources.get("model_call_batch")
    calls = resources.get("model_calls", None if batch else 0)
    return {
        "destinations": sorted(destinations),
        "permissions": sorted(permissions),
        "network": network,
        "cost_micro_usd": cost,
        "model_calls": calls,
        **({"model_call_batch": batch} if batch else {}),
    }


def _describe(state, candidate):
    record, _manifest, _files, _modes, archive_hash, bindings, plan = candidate
    differences = []
    for node, previous in sorted(state["bindings"].items()):
        current = bindings[node]
        if previous == current:
            continue
        before, after = _effects(previous), _effects(current)
        requires_approval = (
            before["destinations"] != after["destinations"]
            or not set(after["permissions"]) <= set(before["permissions"])
            or after["network"]
            and not before["network"]
            or any(
                type(before[key]) is not int
                or type(after[key]) is not int
                or after[key] > before[key]
                for key in ("cost_micro_usd", "model_calls")
            )
        )
        differences.append(
            {
                "node": node,
                "before": before,
                "after": after,
                "needs_approval": requires_approval,
            }
        )
    require(differences, "原綁定已經恢復，請直接續做原 Run。")
    scope = {
        "session_id": state["session_id"],
        "source_run_id": state["run_id"],
        "rebind_work_id": state["rebind"]["work_id"],
        "source_checkpoint": digest(state),
        "frozen_state_hash": _frozen_state_hash(state),
        "candidate_hash": archive_hash,
        "install_hash": record["install_hash"],
        "plan_hash": digest(plan),
        "bindings_hash": digest(bindings),
        "differences": differences,
    }
    return {
        **scope,
        "scope_hash": digest(scope),
        "needs_approval": any(row["needs_approval"] for row in differences),
    }


def preview(service, state):
    require(not _unsettled(state), "仍有宿主回執待查，請先查核原操作。")
    candidate = _read_candidate(service, state)
    if state.get("handoff"):
        scope = state["handoff"]["scope"]
        require(
            candidate[4] == scope["candidate_hash"]
            and candidate[0]["install_hash"] == scope["install_hash"],
            "候選與已保存的啟用範圍不同。",
        )
        return scope
    return _describe(state, candidate)


def staged_task(service, lineage):
    token = lineage["scope_hash"]
    require(bool(re.fullmatch(r"[0-9a-f]{64}", token)), "啟用指紋不合法。")
    folder = service.workspace.runtime.home / "graph_continuations" / token / "package"
    reject_links(folder / "flow.json")
    from ..workspace import Manifest

    manifest = Manifest.model_validate(read_json(folder / "flow.json"))
    task = Task(
        service.workspace.task(manifest.id, read_only=True).folder,
        manifest,
        package_folder=folder,
    )
    require(
        task.fingerprint == lineage["manifest_fingerprint"],
        "續做 Run 的候選 manifest 已變更。",
    )
    task.flow_id = lineage["flow_id"]
    return task


def _stage(service, state, scope, candidate):
    runtime = service.workspace.runtime
    _record, manifest, files, modes, _archive_hash, bindings, plan = candidate
    parent = runtime.home / "graph_continuations" / scope["scope_hash"]
    package = parent / "package"
    reject_links(package)
    for name, content in files.items():
        path = package / name
        reject_links(path)
        if path.is_file():
            require(path.read_bytes() == content, "續做套件檔案已變更。")
        else:
            atomic_bytes(path, content)
            path.chmod(modes[name])
    binding_path = runtime.home / "blueprint_bindings" / (digest(plan) + ".json")
    reject_links(binding_path)
    if binding_path.is_file():
        require(read_json(binding_path) == bindings, "續做綁定已變更。")
    else:
        atomic_json(binding_path, bindings)
    return Task(
        service.workspace.task(manifest.id, read_only=True).folder,
        manifest,
        package_folder=package,
    ), parent


def _has_effect(record):
    return (
        record.get("external_claim")
        or record.get("chain", {}).get("external_claim")
        or record.get("loop", {}).get("external_claim")
    )


def _checked_child(session, session_id, plan, bindings):
    child = GraphSession(session.runtime, session_id)
    reject_links(child.path)
    snapshot = read_json(child.path)
    claimed = [key for key, row in snapshot["nodes"].items() if _has_effect(row)]
    return bool(claimed) and (
        snapshot.get("owner_session") == session.id
        and snapshot["plan"] == plan
        and snapshot["bindings"] == bindings
        and digest(plan) == snapshot["plan_hash"]
        and digest(bindings) == plan["bindings_hash"]
        and not _unsettled(snapshot)
        and all(_accepted_external(child, snapshot, key) for key in claimed)
    )


def _accepted_external(session, state, key):
    record = state["nodes"][key]
    if record.get("external_claim"):
        scope_hash = (record.get("decision") or {}).get("scope_hash")
        if not scope_hash:
            return False
        receipt = read_observation(
            session.runtime.workspace_root, record["external_claim"], scope_hash
        )
        return bool(
            receipt
            and receipt["outcome"] == "succeeded"
            and receipt["outputs_hash"] == record.get("outputs_hash")
        )
    if record.get("chain", {}).get("external_claim"):
        binding = state["bindings"][key]
        return _checked_child(
            session,
            record["chain"]["child_session"],
            binding["chain_plan"],
            binding["chain_bindings"],
        )
    if record.get("loop", {}).get("external_claim"):
        prefix = key + "/"
        bindings = {
            name[len(prefix) :]: binding
            for name, binding in state["bindings"].items()
            if name.startswith(prefix)
        }
        items = [item for item in record["loop"]["items"] if item.get("has_external_claim")]
        return bool(items) and all(
            _checked_child(session, item["child_session"], state["plan"]["children"][key], bindings)
            for item in items
        )
    return True


def _inherited(session, parent, plan, bindings):
    from .rebind import _affected

    _, affected = _affected(session, parent, {"bindings": bindings}, new_lock=True)
    history = {entry["step"]: entry for entry in parent["history"]}
    keep = {}
    for key in plan["order"]:
        record = parent["nodes"][key]
        external = _has_effect(record)
        usable = (
            key not in affected
            and record["status"] == "succeeded"
            and all(dep in keep for dep in plan["dependencies"][key])
            and digest(record.get("outputs")) == record.get("outputs_hash")
            and history.get(key, {}).get("outputs") == record.get("outputs")
            and history.get(key, {}).get("attempt") == record.get("attempt")
        )
        if usable:
            try:
                for token in refs(record["outputs"]):
                    session.assets.resolve(token)
                if external:
                    usable = _accepted_external(session, parent, key)
            except (FlowError, OSError, KeyError, TypeError, ValueError):
                usable = False
        require(
            usable or not external or record.get("error") == "EXTERNAL_NOT_EXECUTED",
            "已領取的外部操作無法安全沿用，請先查證或依授權處理，不能重新派送。",
        )
        if usable:
            keep[key] = record
    return keep


def _seed(child, old, plan, bindings, run_id, lineage, keep):
    if child.path.is_file():
        existing = read_json(child.path)
        require(
            existing.get("lineage") == lineage
            and existing["plan_hash"] == digest(plan)
            and existing["run_id"] == run_id,
            "續做工作檢查點與啟用範圍不同。",
        )
        return
    require(digest(old["inputs"]) == old["inputs_hash"], "原 Run 的輸入指紋已變更。")
    for token in set(refs(old["inputs"])):
        child.assets.resolve(token)
    nodes = {}
    for key in plan["order"]:
        node = {"instance_id": f"{child.id}/{key}", "status": "pending", "attempt": 0}
        if key in keep:
            node = {
                **keep[key],
                "instance_id": node["instance_id"],
                "inherited_from": {
                    "run_id": old["run_id"],
                    "outputs_hash": keep[key]["outputs_hash"],
                },
            }
        nodes[key] = node
    state = {
        "session_id": child.id,
        "owner_session": None,
        "run_id": run_id,
        "lineage": lineage,
        "plan": plan,
        "plan_hash": digest(plan),
        "request_hash": lineage["scope_hash"],
        "offline": old["offline"],
        "inputs": old["inputs"],
        "inputs_hash": old["inputs_hash"],
        "config": old["config"],
        "bindings": bindings,
        "nodes": nodes,
        "pending": None,
        "history": [entry for entry in old["history"] if entry["step"] in keep],
        "audit": [],
        **{key: old[key] for key in ("profile", "document_indexes") if key in old},
    }
    child.checkpoint(
        state,
        None,
        "rebind_inherited",
        parent_run_id=old["run_id"],
        saved_steps=sorted(keep),
    )


def _continue(service, source, old, payload):
    runtime = service.workspace.runtime
    intent = old.get("handoff")
    if intent is None:
        candidate = _read_candidate(service, old)
        scope = _describe(old, candidate)
        require(
            payload.get("scope_hash") == scope["scope_hash"],
            "請先核對本次重綁候選及範圍。",
        )
        require(
            not scope["needs_approval"]
            or isinstance(payload.get("user_text"), str)
            and 0 < len(payload["user_text"].strip()) <= 4000,
            "範圍已改變，請轉交使用者的實際核准。",
        )
    else:
        scope = intent["scope"]
        require(
            payload.get("scope_hash") == scope["scope_hash"],
            "續做請求的核准範圍已變更。",
        )
        require(
            scope["frozen_state_hash"] == _frozen_state_hash(old),
            "原 Run 的交接內容已變更。",
        )
        candidate = _read_candidate(service, old)
        require(
            candidate[4] == scope["candidate_hash"]
            and candidate[0]["install_hash"] == scope["install_hash"],
            "重新取得的候選與已保存的啟用意圖不同。",
        )
    bindings, plan = candidate[5:]
    keep = _inherited(source, old, plan, bindings)
    with closing(runtime.connect()) as db:
        parent = db.execute(
            "SELECT flow_id,job_id FROM runs WHERE run_id=?", (old["run_id"],)
        ).fetchone()
    require(parent is not None, "續做來源 Run 不存在。")
    if intent is None:
        old["handoff"] = {
            "scope": scope,
            "approval": payload.get("user_text") if scope["needs_approval"] else None,
        }
        source.checkpoint(old, None, "rebind_intent", scope_hash=scope["scope_hash"])
    task, location = _stage(service, old, scope, candidate)
    task.flow_id = parent["flow_id"]
    lineage = {
        "parent_session_id": source.id,
        "parent_run_id": old["run_id"],
        "root_run_id": old.get("lineage", {}).get("root_run_id", old["run_id"]),
        "scope_hash": scope["scope_hash"],
        "rebind_work_id": scope["rebind_work_id"],
        "manifest_fingerprint": task.fingerprint,
        "flow_id": task.flow_id,
        "job_id": parent["job_id"],
    }
    ticket = location / "launch.json"
    _write_ticket(ticket, {"rebind_activation": scope["scope_hash"]})
    workflow = task.configuration(runtime)
    run_id, output, _spec = reserve_workflow_run(
        runtime,
        task,
        workflow,
        ticket,
        task.folder / "outputs" / ("rebind-" + scope["scope_hash"]),
        offline=old["offline"],
        idempotency_key="rebind:" + scope["scope_hash"],
        continuation_job_id=parent["job_id"],
    )
    child = GraphSession(runtime, "job_" + run_id.removeprefix("run_"))
    _seed(child, old, plan, bindings, run_id, lineage, keep)
    old["continuation"] = {
        "run_id": run_id,
        "session_id": child.id,
        "scope_hash": scope["scope_hash"],
    }
    source.checkpoint(old, None, "rebind_activated", continuation=old["continuation"])
    return task, workflow, ticket, output, run_id, child


def activate(service, source, payload):
    runtime = service.workspace.runtime
    with runtime.lock("graph-rebind-activation:" + source.id, timeout=10):
        with runtime.lock("blueprint:" + source.id, timeout=10):
            old = read_json(source.path)
            if link := old.get("continuation"):
                require(
                    payload.get("scope_hash") == link["scope_hash"],
                    "續做請求的核准範圍已變更。",
                )
                run = RunStore(runtime).get(link["run_id"])
                require(run is not None, "續作交接缺少保留的 Run。")
                if run["execution_status"] != "created":
                    child = GraphSession(runtime, link["session_id"])
                    require_active_child(runtime, read_json(child.path), verify_run=False)
                    return child.interact("status", {})
            require(
                not old.get("cancelled") and not _unsettled(old),
                "此工作已取消或尚有待查的外部操作。",
            )
            if old.get("handoff") is None:
                require(payload.get("revision") == digest(old), "工作版本已更新，請先重讀。")
                from .capabilities import resolve_graph

                original = resolve_graph(
                    old["plan"]["graph"],
                    runtime.workspace_root,
                    admission=old["bindings"],
                    offline=old["offline"],
                )
                require(
                    original["fingerprint"] != old["plan"]["capability_fingerprint"],
                    "原綁定已恢復，請繼續原 Run。",
                )
            task, workflow, ticket, output, run_id, child = _continue(service, source, old, payload)
        recorded = RunStore(runtime).get(run_id)
        if recorded["execution_status"] == "created":
            report = run_workflow(
                runtime,
                task,
                workflow,
                ticket,
                output,
                offline=old["offline"],
                reserved_run_id=run_id,
                continuation_job_id=recorded["job_id"],
            )
            return {**report["blueprint"], "run_id": run_id, "output": report["output"]}
        return child.interact("status", {})


def require_active_child(runtime, state, *, verify_run=True):
    lineage = state.get("lineage")
    if not lineage:
        return
    parent = GraphSession(runtime, lineage["parent_session_id"])
    source = read_json(parent.path)
    link = source.get("continuation", {})
    require(
        link
        == {
            "session_id": state["session_id"],
            "run_id": state["run_id"],
            "scope_hash": lineage["scope_hash"],
        }
        and source["run_id"] == lineage["parent_run_id"]
        and source["handoff"]["scope"]["plan_hash"] == state["plan_hash"]
        and source["handoff"]["scope"]["bindings_hash"] == state["plan"]["bindings_hash"],
        "續做 Run 尚未取得對應原工作的派送權。",
    )
    if verify_run:
        run = RunStore(runtime).get(state["run_id"])
        require(
            run is not None
            and run["job_id"] == lineage["job_id"]
            and run["execution_status"] in {"running", "publishing", "succeeded"},
            "續做 Run 尚未啟動或已中止，請先檢視執行狀態。",
        )


def launch_pending(runtime, snapshot):
    if not snapshot.get("handoff"):
        return False
    link = snapshot.get("continuation")
    if link is None:
        return True
    run = RunStore(runtime).get(link["run_id"])
    return run is not None and run["execution_status"] == "created"


def follow(runtime, session, snapshot, *, stop_for_launch=False, stop_for_scope=None):
    seen = set()
    while snapshot.get("continuation"):
        if (
            stop_for_launch
            and launch_pending(runtime, snapshot)
            or stop_for_scope is not None
            and snapshot["continuation"]["scope_hash"] == stop_for_scope
        ):
            break
        require(session.id not in seen, "續做工作的關聯包含循環。")
        seen.add(session.id)
        link = snapshot["continuation"]
        session = GraphSession(runtime, link["session_id"])
        reject_links(session.path)
        snapshot = read_json(session.path)
        require_active_child(runtime, snapshot, verify_run=False)
    return session, snapshot


def execution_view(runtime, root_run_id):
    if not root_run_id:
        return None
    session = GraphSession(runtime, "job_" + root_run_id.removeprefix("run_"))
    reject_links(session.path)
    if not session.path.is_file():
        return None
    state = read_json(session.path)
    if not state.get("rebind") or state.get("run_id") != root_run_id:
        return None
    session, active = follow(runtime, session, state)
    view = session.view(active)
    run_id = active["run_id"]
    if view.get("delivery_status") == "published" and view.get("assessment_id"):
        with closing(runtime.connect()) as db:
            row = db.execute(
                """SELECT p.run_id FROM graph_published p JOIN deliveries d USING(run_id)
                WHERE p.assessment_id=? AND p.revision=? AND d.status='published'
                ORDER BY d.published_at DESC LIMIT 1""",
                (view["assessment_id"], view["quality_revision"]),
            ).fetchone()
        if row is not None:
            run_id = row["run_id"]
    status = {"waiting": "waiting", "failed": "failed", "cancelled": "cancelled"}.get(
        view["status"]
    )
    return {
        "root_run_id": root_run_id,
        "active_run_id": active["run_id"],
        "run_id": run_id,
        "rebind_work_id": view.get("rebind_work_id") or state["rebind"].get("work_id"),
        "wait_reason": view.get("code"),
        "status": status,
    }


def deliver(service, session, state, output, token):
    lineage = state["lineage"]
    require_active_child(service.workspace.runtime, state)
    task = staged_task(service, lineage)
    runtime = service.workspace.runtime
    task.flow_id = lineage["flow_id"]
    ticket = session.folder / "delivery.json"
    _write_ticket(ticket, {"session_id": session.id})
    workflow = task.configuration(runtime)
    run_id, output, _spec = reserve_workflow_run(
        runtime,
        task,
        workflow,
        ticket,
        output,
        offline=state["offline"],
        continuation_job_id=lineage["job_id"],
        idempotency_key="rebind-delivery:" + digest([lineage["scope_hash"], token]),
    )
    recorded = RunStore(runtime).get(run_id)
    if recorded["execution_status"] == "succeeded":
        return read_json(runtime.logs / task.flow_id / run_id / "report.json")
    return run_workflow(
        runtime,
        task,
        workflow,
        ticket,
        output,
        offline=state["offline"],
        reserved_run_id=run_id,
        continuation_job_id=lineage["job_id"],
    )
