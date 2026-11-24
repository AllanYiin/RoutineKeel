"""Coordinate local blueprint completion through the existing Builder and Resolver."""

import re
from datetime import UTC, datetime
from zipfile import BadZipFile

from ..runtime import FlowError, digest
from . import COMPLETION_OPERATIONS
from .completion_cases import (
    attach_fixture,
    case_draft,
    case_rows,
    check_case,
    fixture_references,
)
from .completion_delivery import RUNNING, queue_trial, trial_key, worker_alive
from .completion_store import CompletionStore
from .source import require

ACTIVE = {"needs_input", "needs_source", "ready_for_trial", "qualifying", *RUNNING}


def _gap(kind, code, location, reason, actions, **details):
    return {
        "id": digest([kind, location])[:24],
        "kind": kind,
        "code": code,
        "location": location,
        "reason": reason,
        "actions": actions,
        **details,
    }


def _capability_gap(item, decision, outcome):
    actions = ["refresh_environment", "review_compatible_adapter"]
    if decision.get("status") == "needs_qualification":
        actions = ["qualify_adapter", *actions]
    if item["code"] == "OPTIONAL_DECISION_REQUIRED":
        actions.append("review_alternative_delivery")
    report = outcome.get("report") or {}
    if report.get("verdict") == "PASS":
        actions = ["wait_for_other_bindings"]
    elif report.get("conformance", {}).get("code") == "TRIAL_CASES_REQUIRED":
        actions = ["complete_adapter_cases", *actions]
    elif (
        report.get("conformance", {}).get("code")
        == "AUTHORIZED_HOST_QUALIFICATION_REQUIRED"
    ):
        actions = ["provide_host_qualification", *actions]
    return _gap(
        "capability",
        item["code"],
        item["node"],
        item["reason"],
        actions,
        capability=item["tool"],
        candidates=decision.get("candidates", []),
        qualification_scope=outcome.get("scope"),
        qualification_report=outcome.get("report"),
    )


def _preflight(service, state):
    key = state["draft_id"]
    if key is None:
        return state
    report = service.resolve_blueprint_capabilities(key, offline=state["offline"])
    decisions = {item["node"]: item for item in report.get("portable_decisions", [])}
    graph = (
        service.install_workflow(draft_id=key)["source"]["graph"]
        if state["blueprint_schema_version"] == 2
        else None
    )
    attempts = state.get("qualification", {})
    gaps = [
        _capability_gap(
            item, decisions.get(item["node"], {}), attempts.get(item["node"], {})
        )
        for item in report["gaps"]
    ]
    gaps.extend(
        _gap(
            "builder",
            item["code"],
            item.get("node", "graph"),
            item["reason"],
            ["review_pattern_admission", "recheck", "revise_blueprint"]
            if item.get("stage") == "pattern_admission"
            else ["review_compatible_adapter", "revise_blueprint"],
        )
        for item in report.get("build_gaps", [])
        if not item["code"].startswith("TRIAL_CASE")
    )
    rows = []
    if graph is not None:
        _suggest_replacements(service, state, graph, gaps, decisions)
        rows = case_rows(graph, state)
        if len(rows) > 20:
            gaps.append(
                _gap(
                    "case",
                    "TRIAL_CASES_REQUIRED",
                    "cases",
                    "必要驗收案例超過 20 組，請修訂藍圖。",
                    ["revise_blueprint"],
                )
            )
        for row in rows:
            try:
                check_case(service.workspace.root, graph, row["case"])
            except (FlowError, OSError) as exc:
                gap = _gap(
                    "case",
                    "TRIAL_CASE_NOT_EXECUTABLE",
                    row["location"],
                    str(exc),
                    ["complete_case", "attach_fixture"],
                    intent=row["intent"],
                    draft=case_draft(graph) if row["case"] is None else row["case"],
                )
                if row["evidence"] == "blueprint":
                    gap["actions"] = ["attach_fixture", "revise_blueprint"]
                gaps.append(gap)
    if state["blueprint_schema_version"] == 1:
        gaps = _legacy_gaps(service, state)
    for gap in gaps:
        if gap["kind"] != "capability" or not gap["candidates"]:
            continue
        refused = state.get("declined_adapters", {}).get(gap["location"], {})
        gap["candidates"] = [
            {**candidate, "declined": candidate["adapter"] in refused}
            for candidate in gap["candidates"]
        ]
        if not gap.get("requires_design_revision"):
            gap["actions"].extend(["select_adapter", "decline_adapter"])
        if all(row["declined"] for row in gap["candidates"]):
            gap["actions"] = [
                "review_compatible_adapter",
                "revise_blueprint",
                "select_adapter",
            ]
    state = {
        **state,
        "case_coverage": rows,
        "portable_decisions": list(decisions.values()),
        "capability_fingerprint": report["fingerprint"],
    }
    if not gaps:
        try:
            scope = trial_key(service, state)
        except (FlowError, OSError) as exc:
            gaps.append(
                _gap(
                    "fixture",
                    "TRIAL_FIXTURE_UNAVAILABLE",
                    "fixtures",
                    str(exc),
                    ["attach_fixture"],
                )
            )
        else:
            prior = state.get("trial", {})
            if prior.get("outcome") == "failed" and prior.get("key") == scope:
                gaps.append(_trial_gap(state, prior))
    for gap in gaps:
        gap["scope_hash"] = digest([key, report["fingerprint"], gap])
    return {
        **state,
        "status": "needs_input" if gaps else "ready_for_trial",
        "gaps": gaps,
    }


def _suggest_replacements(service, state, graph, gaps, decisions):
    targets = [
        gap
        for gap in gaps
        if gap["kind"] in {"capability", "builder"} and gap["location"] not in decisions
    ]
    if not targets:
        return
    from routinekeel_contracts.graph_validator import validate_blueprint

    from ..graph.portable import replacement_candidates
    from ..graph.qualification import _tasks

    model, _ = validate_blueprint(graph)
    tasks = _tasks(model)
    for gap in targets:
        key = gap["location"]
        if key in tasks:
            candidates = replacement_candidates(
                key, tasks[key], service.workspace.root, offline=state["offline"]
            )
            gap["candidates"] = candidates
            if candidates:
                gap["requires_design_revision"] = True
                gap["actions"] = ["revise_blueprint", *gap["actions"]]


def _legacy_gaps(service, state):
    from ..graph.capabilities import validate_bindings

    recipe = state.get("legacy_recipe")
    if recipe is None:
        return [
            _gap(
                "recipe",
                "LEGACY_RECIPE_REQUIRED",
                "recipe",
                "請補上此線性藍圖的執行方案與案例。",
                ["provide_recipe"],
            )
        ]
    try:
        validate_bindings(
            service.workspace.root,
            recipe["resolution"]["bindings"],
            offline=state["offline"],
        )
    except FlowError as exc:
        return [
            _gap(
                "recipe",
                exc.code,
                "recipe",
                str(exc),
                ["refresh_environment", "provide_recipe"],
            )
        ]
    return []


def _trial_gap(state, trial):
    location = f"case/{trial['case']}" if "case" in trial else "trial"
    supplied = state.get("case_supplies", {})
    if trial.get("phase") == "compare":
        actions = (
            ["complete_case", "revise_blueprint"]
            if location in supplied
            else ["revise_blueprint"]
        )
    else:
        actions = ["retry_trial", "refresh_environment", "revise_blueprint"]
    return _gap(
        "trial",
        trial.get("code", "TRIAL_FAILED"),
        location,
        trial["reason"],
        actions,
        build_id=trial["build_id"],
        phase=trial.get("phase"),
    )


def _qualify(service, state, checkpoint):
    from ..graph.qualification import complete_bindings

    if state["status"] in RUNNING or state["draft_id"] is None:
        return state
    decisions = state.get("adapter_choices", {})
    declined = state.get("declined_adapters", {})
    selections = {}
    for decision in state["portable_decisions"]:
        node = decision["node"]
        choice = decisions.get(node, {})
        allowed = {
            candidate["adapter"]: candidate["environment_fingerprint"]
            for candidate in decision["candidates"]
            if candidate["allowed"]
        }
        refused = declined.get(node, {})
        allowed = {key: value for key, value in allowed.items() if key not in refused}
        automatic = [
            key for key in decision.get("automatic_candidates", []) if key in allowed
        ]
        if decision.get("selected_adapter") in allowed:
            selections[node] = decision["selected_adapter"]
        elif (
            allowed.get(choice.get("adapter")) == choice.get("environment_fingerprint")
            and choice
        ):
            selections[node] = choice["adapter"]
        elif len(automatic) == 1:
            selections[node] = automatic[0]
    if not selections or not any(gap["kind"] == "capability" for gap in state["gaps"]):
        return state
    checkpoint(
        {**state, "status": "qualifying", "qualification_selections": selections}
    )
    try:
        result = complete_bindings(
            service.workspace,
            state["draft_id"],
            selections,
            state.get("qualification", {}),
            offline=state["offline"],
            case_supplies=state.get("adapter_case_supplies"),
        )
    except FlowError as exc:
        if exc.code not in {
            "BINDING_STALE",
            "CAPABILITY_UNAVAILABLE",
            "PERMISSION_DENIED",
            "VERSION_MISMATCH",
        }:
            raise
        return _preflight(service, {**state, "qualification": {}})
    return _preflight(
        service,
        {
            **state,
            "qualification": result["outcomes"],
            "environment_lock": result["lock"],
        },
    )


def _start(service, payload, work_id):
    require(
        set(payload) <= {"archive", "draft_id", "request_id", "intent", "offline"},
        "藍圖補強的啟動參數不符。",
    )
    archive, key = payload.get("archive"), payload.get("draft_id")
    intent, offline = payload.get("intent", "build"), payload.get("offline", False)
    require(bool(archive) != bool(key), "請指定藍圖附件或草稿識別碼。")
    require(
        intent in ("build", "install") and type(offline) is bool,
        "補強目標或離線設定無效。",
    )
    state = {
        "work_id": work_id,
        "intent": intent,
        "offline": offline,
    }
    return _receive_source(service, state, archive, key)


def _receive_source(service, state, archive, key):
    try:
        if archive:
            imported = service.install_workflow(archive=archive)
            key = imported["draft_id"]
        source = service.install_workflow(draft_id=key)["source"]
    except (FlowError, BadZipFile, OSError) as exc:
        reason = str(exc) if isinstance(exc, FlowError) else "無法讀取此藍圖附件。"
        gap = _gap(
            "source", "BLUEPRINT_SOURCE_REQUIRED", "archive", reason, ["replace_source"]
        )
        gap["scope_hash"] = digest([state["work_id"], gap])
        return {**state, "status": "needs_source", "draft_id": None, "gaps": [gap]}
    return _preflight(
        service,
        {
            **state,
            "source_draft_id": key,
            "draft_id": key,
            "workflow_id": source["manifest"]["id"],
            "blueprint_schema_version": source.get("blueprint_schema_version", 1),
        },
    )


def _change(service, current, operation, payload):
    if operation == "submit":
        return _submit(service, current, payload)
    require(set(payload) == {"request_id", "expected_revision"}, "補強請求的參數不符。")
    status = current["status"]
    if operation == "recheck" and status == "needs_update":
        from .completion_update import update_details

        return {
            **current,
            "update": update_details(service, current, current["update_plan"]),
        }
    if (
        operation in {"advance", "recheck"}
        and status in RUNNING
        and worker_alive(current)
    ):
        return current
    if (operation in {"advance", "recheck"} and status in ACTIVE | {"verified"}) or (
        operation == "resume" and status == "paused"
    ):
        return _preflight(service, current)
    transitions = {
        "pause": (ACTIVE, "paused"),
        "cancel": (ACTIVE | {"paused"}, "cancelled"),
        "archive": (
            {"cancelled", "paused", "verified", "installed", "needs_update"},
            "archived",
        ),
    }
    allowed, target = transitions.get(operation, (set(), None))
    require(status in allowed, "這項補強工作目前無法執行指定操作。")
    return {**current, "status": target}


def _submit(service, current, payload):
    require(
        current["status"] in (ACTIVE - RUNNING) | {"needs_update"},
        "請先恢復或等待這項補強工作。",
    )
    action = payload.get("action", "select_adapter")
    if action in {"compare_update", "activate_update"}:
        from .completion_update import submit_update

        return submit_update(service, current, payload)
    if action == "attach_fixture":
        requested = payload.get("requested_path")
        if requested is not None:
            live = _preflight(service, current)
            gap = next(
                (item for item in live["gaps"] if item["id"] == payload.get("gap_id")),
                None,
            )
            require(
                gap
                and gap["scope_hash"] == payload.get("scope_hash")
                and isinstance(requested, str)
                and requested in set(fixture_references(gap.get("draft"))),
                "請確認這個素材對應目前驗收案例要求的檔案位置。",
            )
        fixture = attach_fixture(service, current, payload.get("source"), requested)
        return _preflight(
            service,
            {
                **current,
                "fixtures": {**current.get("fixtures", {}), fixture["id"]: fixture},
            },
        )
    if action == "adopt_revision":
        return _adopt_revision(service, current, payload)
    if action == "replace_source":
        require(
            current["status"] == "needs_source"
            and payload.get("scope_hash") == current["gaps"][0]["scope_hash"]
            and isinstance(payload.get("archive"), str)
            and bool(payload["archive"].strip()),
            "請指定這一項缺口的新藍圖附件。",
        )
        return _receive_source(service, current, payload["archive"], None)
    require(
        isinstance(payload.get("user_text"), str)
        and 0 < len(payload["user_text"].strip()) <= 4000,
        "請附上使用者實際的補強答覆。",
    )
    live = _preflight(service, current)
    gap = next(
        (item for item in live["gaps"] if item["id"] == payload.get("gap_id")), None
    )
    if gap is None or gap["scope_hash"] != payload.get("scope_hash"):
        raise FlowError("REVISION_CONFLICT", "缺口或候選已改變，請先重驗。")
    if action == "complete_case":
        return _supply_case(service, live, gap, payload)
    if action == "retry_trial":
        require(action in gap["actions"], "此試做結果需要修訂或補件。")
        return _preflight(
            service, {**live, "trial": {}, "retry_answer": payload["user_text"]}
        )
    if action == "provide_recipe":
        from .recipe import compile_plan

        require(action in gap["actions"], "目前缺口不接受線性流程執行方案。")
        resolution, cases = payload.get("resolution"), payload.get("cases")
        require(
            isinstance(resolution, dict)
            and isinstance(cases, list)
            and 1 <= len(cases) <= 20,
            "請提供完整執行方案與 1–20 組案例。",
        )
        compile_plan(
            service.install_workflow(draft_id=live["draft_id"])["source"], resolution
        )
        return _preflight(
            service,
            {
                **live,
                "legacy_recipe": {"resolution": resolution, "cases": cases},
                "recipe_confirmation": payload["user_text"],
            },
        )
    if action == "complete_adapter_cases":
        require(action in gap["actions"], "目前缺口沒有等待 Adapter 測試案例。")
        service_result = {
            **live.get("adapter_case_supplies", {}),
            gap["location"]: payload.get("cases"),
        }
        from ..graph.qualification import validate_adapter_cases

        validate_adapter_cases(
            service.workspace, live["draft_id"], gap["location"], payload.get("cases")
        )
        return _preflight(
            service,
            {
                **live,
                "adapter_case_supplies": service_result,
                "adapter_case_confirmation": {
                    **live.get("adapter_case_confirmation", {}),
                    gap["location"]: payload["user_text"],
                },
            },
        )
    require(action in ("select_adapter", "decline_adapter"), "不支援這項補強回覆。")
    require(
        not gap.get("requires_design_revision"), "替換已指定的工具須先確認修訂藍圖。"
    )
    candidate = next(
        (
            row
            for row in gap.get("candidates", [])
            if row["adapter"] == payload.get("adapter")
            and (action == "decline_adapter" or row["allowed"])
        ),
        None,
    )
    require(candidate is not None, "請選擇目前已觀測且有使用權限的 Adapter。")
    choice = {
        "adapter": candidate["adapter"],
        "environment_fingerprint": candidate["environment_fingerprint"],
        "scope_hash": gap["scope_hash"],
        "user_text": payload["user_text"],
    }
    node = gap["location"]
    choices = dict(current.get("adapter_choices", {}))
    declined = dict(current.get("declined_adapters", {}))
    refusals = dict(declined.get(node, {}))
    if action == "decline_adapter":
        refusals[candidate["adapter"]] = choice
        if choices.get(node, {}).get("adapter") == candidate["adapter"]:
            choices.pop(node)
    else:
        refusals.pop(candidate["adapter"], None)
        choices[node] = choice
    declined[node] = refusals
    return _preflight(
        service, {**live, "adapter_choices": choices, "declined_adapters": declined}
    )


def _supply_case(service, state, gap, payload):
    require("complete_case" in gap["actions"], "已確認案例的變更須先修訂藍圖。")
    graph = service.install_workflow(draft_id=state["draft_id"])["source"]["graph"]
    case = payload.get("case")
    check_case(service.workspace.root, graph, case)
    supplements = {
        **state.get("case_supplies", {}),
        gap["location"]: {
            "case": case,
            "user_text": payload["user_text"],
            "scope_hash": gap["scope_hash"],
        },
    }
    return _preflight(service, {**state, "case_supplies": supplements})


def _revision_preview(service, current, draft_id):
    base = service.install_workflow(draft_id=current["draft_id"])["source"].get("graph")
    candidate = service.install_workflow(draft_id=draft_id)["source"].get("graph")
    require(
        base
        and candidate
        and candidate["workflow"]["id"] == base["workflow"]["id"]
        and candidate["workflow"]["revision"] > base["workflow"]["revision"]
        and candidate["provenance"].get("lineage", {}).get("base_source_hash")
        == current["draft_id"]
        and candidate["provenance"].get("design_review"),
        "請先從 Studio 匯出已確認、且源自目前藍圖的較新修訂。",
    )
    changed = {
        key: {"from": base.get(key), "to": candidate.get(key)}
        for key in sorted(base.keys() | candidate.keys())
        if key != "provenance" and base.get(key) != candidate.get(key)
    }
    result = {
        "work_id": current["work_id"],
        "source": current["draft_id"],
        "candidate": draft_id,
        "changes": changed,
    }
    return {**result, "scope_hash": digest(result)}


def _adopt_revision(service, current, payload):
    preview = _revision_preview(service, current, payload.get("draft_id"))
    require(
        preview["scope_hash"] == payload.get("scope_hash")
        and isinstance(payload.get("user_text"), str)
        and 0 < len(payload["user_text"].strip()) <= 4000,
        "請確認這次藍圖修訂的實際差異。",
    )
    state = {
        key: value
        for key, value in current.items()
        if key
        not in {
            "case_supplies",
            "adapter_choices",
            "declined_adapters",
            "qualification",
            "environment_lock",
            "adapter_case_supplies",
            "adapter_case_confirmation",
            "candidate",
            "trial",
            "update_plan",
        }
    }
    return _preflight(
        service,
        {
            **state,
            "draft_id": preview["candidate"],
            "design_revision": {
                "scope_hash": preview["scope_hash"],
                "user_text": payload["user_text"],
            },
        },
    )


def completion(service, operation, work_id=None, payload=None):
    require(operation in COMPLETION_OPERATIONS, "未支援的藍圖補強操作。")
    require(payload is None or isinstance(payload, dict), "補強參數須為物件。")
    payload = dict(payload or {})
    runtime = service.workspace.runtime
    store = CompletionStore(runtime)
    if operation != "start":
        require(
            isinstance(work_id, str)
            and re.fullmatch(r"completion_[0-9a-f]{32}", work_id),
            "補強工作識別碼無效。",
        )
    if operation in {"status", "history"}:
        require(not payload, "查詢補強狀態只接受工作識別碼。")
        if operation == "history":
            return store.history(work_id)
        current = store.read(work_id)
        if current["status"] in RUNNING:
            from .completion_delivery import trial_progress

            return {**current, "trial_progress": trial_progress(service, current)}
        return current
    if operation == "preview_revision":
        require(set(payload) == {"draft_id"}, "請指定已從 Studio 匯入的修訂識別碼。")
        return _revision_preview(service, store.read(work_id), payload["draft_id"])

    request_id = payload.get("request_id")
    require(
        isinstance(request_id, str)
        and 0 < len(request_id.strip()) <= 128
        and not request_id.startswith("internal:"),
        "請提供補強請求識別碼。",
    )
    if operation == "start":
        require(work_id is None, "建立補強工作時由底座分配識別碼。")
        work_id = "completion_" + digest(request_id)[:32]
    request_hash = digest([operation, payload])
    with runtime.lock("blueprint-completion:" + work_id, timeout=10):
        replay = store.replay(work_id, request_id, request_hash)
        if replay is not None:
            return replay
        current = store.find(work_id) if operation == "start" else store.read(work_id)
        if operation == "start":
            if current is not None and current["start_hash"] != request_hash:
                raise FlowError("REVISION_CONFLICT", "同一補強啟動請求已用於其他內容。")
            updated = (
                _preflight(service, current)
                if current
                else {
                    **_start(service, payload, work_id),
                    "start_hash": request_hash,
                }
            )
        else:
            if (
                type(payload.get("expected_revision")) is not int
                or payload["expected_revision"] != current["revision"]
            ):
                raise FlowError(
                    "REVISION_CONFLICT", "補強工作已更新，請先重新讀取狀態。"
                )
            updated = _change(service, current, operation, payload)
        revision = current["revision"] if current else 0

        def record(state, identifier=None):
            nonlocal revision
            revision += 1
            value = {
                **state,
                "revision": revision,
                "updated_at": datetime.now(UTC).isoformat(),
            }
            return store.save(value, identifier or f"internal:{revision}", request_hash)

        if operation in {"start", "advance", "submit", "recheck", "resume"}:
            updated = _qualify(service, updated, record)
            updated = queue_trial(service, updated, record)
        return record(updated, request_id)
