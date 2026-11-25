"""Coordinate local blueprint completion through the existing Builder and Resolver."""

import re
from datetime import UTC, datetime
from zipfile import BadZipFile

from ..runtime import FlowError, digest
from . import COMPLETION_OPERATIONS
from .completion_cases import (
    attach_fixture,
    case_draft,
    case_review,
    case_rows,
    check_case,
    fixture_references,
)
from .completion_delivery import RUNNING, queue_trial, trial_key, worker_alive
from .completion_store import CompletionStore
from .source import require

ACTIVE = {"needs_input", "needs_source", "ready_for_trial", "qualifying", "preparing", *RUNNING}


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


def _capability_gap(item, decision, outcome, choice, refused):
    actions = ["refresh_environment", "review_compatible_adapter"]
    if decision.get("status") == "needs_qualification":
        actions = ["qualify_adapter", *actions]
    if item["code"] == "OPTIONAL_DECISION_REQUIRED":
        actions.append("review_alternative_delivery")
    report = outcome.get("report") or {}
    reported_adapter = report.get("adapter")
    if report:
        accepted = any(
            candidate["allowed"] and candidate["adapter"] == reported_adapter
            and candidate["environment_fingerprint"] == report.get("environment_fingerprint")
            for candidate in decision.get("candidates", [])
        )
        if not accepted or reported_adapter in refused or choice.get("adapter", reported_adapter) != reported_adapter:
            report = {}
    qualified = report.get("verdict") == "PASS"
    if qualified:
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
        "WAITING_FOR_OTHER_BINDINGS" if qualified else item["code"],
        item["node"],
        "本步驟已通過資格驗證。等其餘必要步驟完成後即可鎖定整份工作流。" if qualified else item["reason"],
        actions,
        capability=item["tool"],
        requirement_id=decision.get("requirement_id"),
        candidates=decision.get("candidates", []),
        candidate_rejections=decision.get("rejections", []),
        observation=decision.get("observation"),
        qualification_scope=outcome.get("scope") if report else None,
        qualification_report=report or None,
    )


def _preflight(service, state, *, advance_search=False):
    key = state["draft_id"]
    if key is None:
        return state
    from ..graph.toolchain_search import PlanningSearch

    search = PlanningSearch(
        service.workspace.runtime, state.get("toolchain_searches", {}), advance=advance_search,
        selections=state.get("adapter_choices"),
        suggestions=state.get("toolchain_suggestions"),
    )
    report = service.resolve_blueprint_capabilities(key, offline=state["offline"], planning_search=search)
    decisions = {item["node"]: item for item in report.get("portable_decisions", [])}
    graph = (
        service.install_workflow(draft_id=key)["source"]["graph"]
        if state["blueprint_schema_version"] == 2
        else None
    )
    attempts = state.get("qualification", {})
    choices = state.get("adapter_choices", {})
    refused = state.get("declined_adapters", {})
    gaps = [
        _capability_gap(item, decisions.get(item["node"], {}), attempts.get(item["node"], {}),
                        choices.get(item["node"], {}), refused.get(item["node"], {}))
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
        from .host_preparation import offers

        _suggest_replacements(service, state, graph, gaps, decisions)
        offers(service, state, graph, gaps)
        for gap in gaps:
            if gap["code"] == "WAITING_FOR_OTHER_BINDINGS":
                continue
            planning = report.get("toolchain_planning", {}).get(gap["location"])
            if gap["kind"] == "capability" and planning is not None:
                gap["toolchain_planning"] = planning
                if planning["status"] not in {"needs_port_semantics", "not_substitutable"}:
                    gap["actions"].append("propose_toolchain")
                if planning["status"] == "search_limit_reached":
                    gap["actions"].append("advance_toolchain_search")
            preparations = state.get("preparations", {}).get(gap["location"], {})
            if gap["kind"] == "capability" and preparations:
                gap["preparation"] = preparations
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
        deferred = state.get("case_strategy", {}).get("mode") == "observe_runtime"
        for row in [] if deferred else rows:
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
                if row["evidence"] == "pending":
                    gap["actions"].insert(0, "propose_cases")
                    if "proposal" in row:
                        gap["draft"] = row["proposal"]
                        gap["actions"].append("confirm_cases")
                if row["evidence"] == "blueprint":
                    gap["actions"] = ["attach_fixture", "revise_blueprint"]
                gaps.append(gap)
    if state["blueprint_schema_version"] == 1:
        gaps = _legacy_gaps(service, state)
    for gap in gaps:
        if gap["kind"] != "capability" or not gap["candidates"] or gap["code"] == "WAITING_FOR_OTHER_BINDINGS":
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
    if state.get("host_operations"):
        from .host_preparation import append_waiting

        append_waiting(state, gaps, _gap)
    if graph is not None:
        from . import binding_options

        tasks, rules = binding_options.describe(service.workspace.root, graph, state, gaps)
    state = {
        **state,
        "case_coverage": rows,
        "portable_decisions": list(decisions.values()),
        "capability_observation": report.get("capability_observation"),
        "capability_fingerprint": report["fingerprint"],
        "toolchain_searches": search.references,
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
    updated = {
        **state,
        "status": "needs_input" if gaps else "ready_for_trial",
        "gaps": gaps,
    }
    if graph is not None:
        from .adapter_development import work_orders

        updated["binding_plan"] = binding_options.binding_plan(
            gaps, state.get("binding_selections", {}), decisions.values()
        )
        updated["adapter_development"] = work_orders(updated, tasks, rules)
        updated["skill_source_findings"] = report.get("skill_source_findings", [])
        review = case_review(rows, gaps, deferred=deferred)
        review["scope_hash"] = digest([state["work_id"], key, [row["intent"] for row in rows]])
        if state["intent"] == "install" and any(row["intent"] is not None for row in rows):
            review["strategies"] = [
                {"id": "synthetic_trial", "label": "先用合成案例試做再安裝"},
            ]
            review["selected_strategy"] = state.get("case_strategy", {}).get("mode")
            review["display_text"] += (
                "\n\n看完以上文字案例後選擇\n"
                "1. 先用合成案例試做再安裝。"
            )
            installed = service.workspace.discover(read_only=True)
            if state["workflow_id"] not in installed:
                review["strategies"].append(
                    {"id": "observe_runtime", "label": "先安裝，於實際執行時驗收"}
                )
                review["default_strategy"] = "observe_runtime"
                review["display_text"] += "\n2. 先安裝，於實際執行時驗收（預設）。"
            else:
                review["default_strategy"] = "synthetic_trial"
        updated["case_review"] = review
    return updated


def _suggest_replacements(service, state, graph, gaps, decisions):
    targets = [
        gap
        for gap in gaps
        if gap["kind"] in {"capability", "builder"} and gap["location"] not in decisions
    ]
    if not targets:
        return
    from routinekeel_contracts.graph_validator import validate_blueprint

    from ..graph.portable import replacement_decision
    from ..graph.qualification import _tasks

    model, _ = validate_blueprint(graph)
    tasks = _tasks(model)
    for gap in targets:
        key = gap["location"]
        if key in tasks:
            decision = replacement_decision(
                key, tasks[key], service.workspace.root, offline=state["offline"]
            )
            gap["candidates"] = decision["candidates"]
            gap["candidate_rejections"] = decision["rejections"]
            gap["observation"] = decision.get("observation")
            if gap["candidates"]:
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


def _prepare(service, state, checkpoint):
    if state.get("draft_id") is None or state.get("blueprint_schema_version") != 2 or state["status"] in RUNNING:
        return state
    if not any(row["kind"] == "capability" and any(
        item.get("recipe_id") for item in row.get("candidate_rejections", [])
    ) for row in state["gaps"]):
        return state
    from .completion_preparation import prepare_missing_adapters

    updated = prepare_missing_adapters(service, state, checkpoint)
    return _preflight(service, updated) if updated is not state else state


def _qualify(service, state, checkpoint):
    from ..graph.qualification import complete_bindings

    if state["status"] in RUNNING or state["draft_id"] is None:
        return state
    decisions = state.get("adapter_choices", {})
    declined = state.get("declined_adapters", {})
    selections = {}
    toolchain_plans = {}
    selected_local_writes = set()
    hosted = {
        row["node"] for row in state.get("binding_plan", {}).get("automatic_work", [])
        if row["handled_by"] == "host_model"
    }
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
            selected_local_writes.add(node)
        elif len(automatic) == 1:
            selections[node] = automatic[0]
        elif node in hosted:
            eligible_hosts = [
                row["adapter"] for row in decision["candidates"]
                if row["adapter"] in allowed and row["locus"] == "host_mediated"
                and row["cost_micro_usd"] == 0 and not row["permission_scope"]
            ]
            if len(eligible_hosts) == 1:
                selections[node] = eligible_hosts[0]
        for candidate in decision["candidates"]:
            if candidate["adapter"] == selections.get(node) and candidate.get("toolchain_plan"):
                toolchain_plans[node] = candidate["toolchain_plan"]
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
            toolchain_plans=toolchain_plans,
            selected_local_writes=selected_local_writes,
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
        return _preflight(service, current, advance_search=operation in {"advance", "resume"})
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
    from .host_preparation import ACTIONS
    from .host_preparation import submit as submit_host_setup

    action = payload.get("action", "select_adapter")
    if action in ACTIONS:
        require(current["status"] not in RUNNING and (
            current["status"] not in {"paused", "cancelled", "archived"} or action == "reconcile_host_preparation"
        ), "目前工作狀態不可派送宿主配置。")
        return submit_host_setup(service, current, payload)
    require(
        current["status"] in (ACTIVE - RUNNING) | {"needs_update"},
        "請先恢復或等待這項補強工作。",
    )
    if action == "propose_toolchain":
        return _suggest_toolchain(service, current, payload)
    if action == "propose_bindings":
        from .binding_options import research

        live = _preflight(service, current)
        return _preflight(service, research(live, payload.get("proposals")))
    if action == "propose_cases":
        return _batch_cases(service, _preflight(service, current), payload, confirm=False)
    if action == "complete_local_adapter":
        from .adapter_development import finish_local

        live = _preflight(service, current)
        return _preflight(service, finish_local(service, live, payload))
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
    if action == "choose_case_strategy":
        review = live.get("case_review", {})
        mode = payload.get("mode")
        require(
            live["intent"] == "install" and review.get("strategies")
            and isinstance(payload.get("scope_hash"), str)
            and payload["scope_hash"] == review["scope_hash"]
            and mode in tuple(item["id"] for item in review["strategies"])
            and (mode == "synthetic_trial" or live.get("trial", {}).get("outcome") != "failed"),
            "請先展示目前的文字案例並取得實際選擇。已失敗的試做須先處理。",
        )
        return _preflight(service, {**live, "case_strategy": {
            "mode": mode, "scope_hash": review["scope_hash"], "user_text": payload["user_text"],
        }})
    if action == "select_bindings":
        return _select_bindings(service, live, payload)
    if action == "confirm_cases":
        return _batch_cases(service, live, payload, confirm=True)
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
    return _preflight(service, _adapter_selection(live, gap, payload, action))


def _adapter_selection(state, gap, payload, action):
    require(
        gap["kind"] == "capability" and not gap.get("requires_design_revision"),
        "替換已指定的工具須先確認修訂藍圖。"
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
        **({"toolchain_plan": candidate["toolchain_plan"]} if candidate.get("toolchain_plan") else {}),
    }
    node = gap["location"]
    choices = dict(state.get("adapter_choices", {}))
    declined = dict(state.get("declined_adapters", {}))
    refusals = dict(declined.get(node, {}))
    if action == "decline_adapter":
        refusals[candidate["adapter"]] = choice
        if choices.get(node, {}).get("adapter") == candidate["adapter"]:
            choices.pop(node)
    else:
        refusals.pop(candidate["adapter"], None)
        choices[node] = choice
    declined[node] = refusals
    return {**state, "adapter_choices": choices, "declined_adapters": declined}


def _select_bindings(service, live, payload):
    from pydantic import ValidationError

    from .binding_options import BindingSelection

    rows = payload.get("selections")
    require(isinstance(rows, list) and 0 < len(rows) <= 50, "請勾選至少一項工具方案。")
    gaps = {gap["id"]: gap for gap in live["gaps"]}
    answers = dict(live.get("binding_selections", {}))
    seen = set()
    for raw in rows:
        try:
            selection = BindingSelection.model_validate(raw)
        except ValidationError as error:
            raise FlowError("INVALID_CONFIGURATION", "工具選擇格式無效。") from error
        gap = gaps.get(selection.gap_id, {})
        binding = gap.get("binding_options")
        if not binding or selection.scope_hash != gap["scope_hash"]:
            raise FlowError("REVISION_CONFLICT", "工具候選已改變，請先重讀目前提案。")
        require(selection.gap_id not in seen, "每個能力只能勾選一項工具方案。")
        seen.add(selection.gap_id)
        option = next((row for row in binding["options"] if row["id"] == selection.option_id), None)
        require(option and option.get("selectable", True), "請選擇目前可採用的工具方案。")
        key = gap["location"]
        answers[key] = {
            "option_id": option["id"], "scope_hash": selection.scope_hash,
            "user_text": payload["user_text"],
        }
        if option["approach"] == "observed_adapter":
            live = _adapter_selection(
                live, gap, {**payload, "adapter": option["adapter"]}, "select_adapter"
            )
        elif key in live.get("adapter_choices", {}):
            live = {**live, "adapter_choices": {
                node: choice for node, choice in live["adapter_choices"].items() if node != key
            }}
    return _preflight(service, {**live, "binding_selections": answers})


def _suggest_toolchain(service, current, payload):
    from pydantic import ValidationError
    from routinekeel_contracts.graph_validator import validate_blueprint
    from routinekeel_contracts.pattern_composition import (
        graph_requirements,
        graph_task_paths,
    )

    from ..graph.capabilities import capability_observation
    from ..graph.toolchain_plan import (
        chain_candidate,
        normalize_suggestion,
        observed_tools,
    )

    live = _preflight(service, current)
    gap = next((item for item in live["gaps"] if item["id"] == payload.get("gap_id")), None)
    require(gap is not None and gap["scope_hash"] == payload.get("scope_hash")
            and "propose_toolchain" in gap["actions"], "請指向目前可提出工具鏈的能力缺口。")
    graph, _ = validate_blueprint(service.install_workflow(draft_id=live["draft_id"])["source"]["graph"])
    node = gap["location"]
    task, rule = graph_task_paths(graph)[node], graph_requirements(graph)[node]
    inventory, observation = capability_observation(service.workspace.root)
    require(gap["toolchain_planning"]["observation_id"] == observation["id"], "工具清冊已更新，請重讀缺口。")
    operations = list(observed_tools(service.workspace.root, inventory))
    try:
        plan = normalize_suggestion(payload.get("proposal"), task, rule, operations, offline=live["offline"])
    except ValidationError as exc:
        raise FlowError("INVALID_CONFIGURATION", "工具鏈提案格式不符。") from exc
    candidate, _binding = chain_candidate(
        task, rule, plan, operations, graph.workflow.model_dump(mode="json"), service.workspace.root,
        offline=live["offline"],
    )
    suggestions = dict(live.get("toolchain_suggestions", {}))
    previous = suggestions.get(node, [])
    plan = candidate["toolchain_plan"]
    if all(row["plan_id"] != plan["plan_id"] for row in previous):
        require(len(previous) < 5, "這個需求已有五項提案，請先評估現有候選。")
        suggestions[node] = [*previous, plan]
    return _preflight(service, {**live, "toolchain_suggestions": suggestions})


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


def _batch_cases(service, live, payload, *, confirm):
    field = "cases" if confirm else "proposals"
    entries = payload.get(field)
    require(isinstance(entries, list) and 0 < len(entries) <= 20, "請提供本輪可執行的驗收案例。")
    graph = service.install_workflow(draft_id=live["draft_id"])["source"]["graph"]
    gaps = {gap["id"]: gap for gap in live["gaps"] if gap["kind"] == "case"}
    proposals = dict(live.get("case_proposals", {}))
    supplies = dict(live.get("case_supplies", {}))
    seen = set()
    for entry in entries:
        require(isinstance(entry, dict) and isinstance(entry.get("gap_id"), str), "驗收補件識別無效。")
        gap = gaps.get(entry["gap_id"], {})
        action = "confirm_cases" if confirm else "propose_cases"
        if gap.get("scope_hash") != entry.get("scope_hash") or action not in gap.get("actions", []):
            raise FlowError("REVISION_CONFLICT", "案例或補件範圍已變更，請重新讀取。")
        key = gap["location"]
        require(key not in seen, "同一案例在這一批只能提交一次。")
        seen.add(key)
        case = proposals[key] if confirm else entry.get("case")
        check_case(service.workspace.root, graph, case)
        if confirm:
            supplies[key] = {"case": case, "user_text": payload["user_text"], "scope_hash": gap["scope_hash"]}
            proposals.pop(key)
        else:
            proposals[key] = case
    return _preflight(service, {**live, "case_proposals": proposals, "case_supplies": supplies})


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
            "case_proposals",
            "case_strategy",
            "adapter_choices",
            "declined_adapters",
            "binding_selections",
            "binding_research",
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


def completion(service, operation, work_id=None, payload=None, *, origin=None):
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
    request_hash = digest([operation, payload] if origin is None else [operation, payload, origin])
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
                    **({"origin": origin} if origin is not None else {}),
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

        dispatch = updated.pop("_host_dispatch", None)
        if dispatch is not None:
            return {**record(updated, request_id), "host_dispatch": dispatch}
        if operation in {"start", "advance", "submit", "recheck", "resume"} and updated["status"] not in {
            "paused", "cancelled", "archived", "installed",
        }:
            updated = _prepare(service, updated, record)
            updated = _qualify(service, updated, record)
            updated = queue_trial(service, updated, record)
        return record(updated, request_id)
