"""Scoped, resumable host setup operations for blueprint completion."""

import re
from datetime import UTC, datetime
from typing import Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import Field, JsonValue, ValidationError
from referencing import Registry
from referencing.exceptions import Unresolvable
from routinekeel_contracts.bindings import ImplementationAdapter
from routinekeel_contracts.capability_requirements import AssetID, AssetVersion
from routinekeel_contracts.graph_validator import validate_blueprint
from routinekeel_contracts.models import Contract
from routinekeel_contracts.pattern_composition import (
    graph_requirements,
    graph_task_paths,
)
from routinekeel_contracts.privacy import contains_private_data

from ..graph.capabilities import capability_observation, live_tool
from ..graph.external import read_observation
from ..graph.portable import _contract_rejections
from ..graph.toolchain_plan import observed_tools, search_toolchains
from ..outputs import reject_links
from ..runtime import FlowError, atomic_json, digest, read_json
from .source import require

PENDING = {"claimed", "receipt_verified", "partial"}
ACTIONS = {
    "authorize_host_preparation",
    "claim_host_preparation",
    "reconcile_host_preparation",
    "request_host_compensation",
}


class SetupAction(Contract):
    tool: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=200)
    destination: str = Field(min_length=1, max_length=256)
    arguments: dict[str, JsonValue]
    permissions: list[str]
    cost_micro_usd: int = Field(ge=0)


class SetupRecipe(Contract):
    recipe_schema_version: Literal[1]
    id: AssetID
    version: AssetVersion
    configure: SetupAction
    compensate: SetupAction | None = None


def _fingerprint(record):
    return record.get("configuration_fingerprint") if isinstance(record, dict) else None


def _valid_fingerprint(value):
    return value is None or isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _scope(
    service, state, node, tool, recipe, catalog, observation, *, original=None, replaces=None
):
    action = recipe.compensate if original else recipe.configure
    require(action is not None and not state["offline"], "此環境不允許該宿主配置。")
    binding = live_tool(service.workspace.root, action.tool, action.version, catalog=catalog)
    resources = binding.get("resources") or {}
    cost = resources.get("cost_micro_usd") if isinstance(resources, dict) else None
    require(
        binding.get("side_effect") == "external"
        and binding.get("destination") == action.destination
        and sorted(binding.get("permissions") or []) == sorted(action.permissions)
        and type(cost) is int
        and cost == action.cost_micro_usd
        and resources.get("model_calls", 0) == 0
        and not contains_private_data(action.arguments),
        "具名配置的權限、費用、目的地或資料不符。",
    )
    schema = binding.get("input_schema")
    try:
        Draft202012Validator.check_schema(schema)
        valid = Draft202012Validator(schema, registry=Registry()).is_valid(action.arguments)
    except (SchemaError, Unresolvable, TypeError, ValueError, RecursionError):
        valid = False
    require(valid, "配置參數不符合宿主工具觀測的契約。")
    target = catalog[tool]
    identity = {
        field: target.get(field) for field in ("source_id", "tool", "version", "destination")
    }
    require(
        identity["tool"] == tool and identity["destination"] == action.destination,
        "配置與目標服務不符。",
    )
    require(_valid_fingerprint(_fingerprint(target)), "服務的配置指紋無效。")
    if original is None:
        require(target.get("available") is not True, "服務已就緒，配置範圍已變更。")
    if original:
        require(
            _fingerprint(target) is not None
            and _fingerprint(target) == original["receipt"]["configuration_fingerprint"],
            "配置狀態已改變，請查證後再補償。",
        )
    scope = {
        "work_id": state["work_id"],
        "draft_id": state["draft_id"],
        "node": node,
        "target": identity,
        "target_hash": digest(identity),
        "recipe_hash": digest(recipe.model_dump(mode="json")),
        "recipe_id": recipe.id,
        "action": "compensate" if original else "configure",
        "tool": action.tool,
        "version": action.version,
        "destination": action.destination,
        "arguments": action.arguments,
        "permissions": sorted(action.permissions),
        "cost_micro_usd": action.cost_micro_usd,
        "observed_tool": binding["receipt_id"],
        "source_id": binding.get("source_id"),
        "provider_tool": binding.get("provider_tool"),
        "prior_configuration": _fingerprint(target),
        "compensates": original["operation_id"] if original else None,
        "restore_configuration": original["scope"]["prior_configuration"] if original else None,
        "replaces": replaces,
    }
    require(not contains_private_data(scope), "配置範圍含私密資料，請由宿主保留憑證。")
    return {
        "operation_id": digest(scope),
        "scope_hash": digest(scope),
        "scope": scope,
        "observation_id": observation["id"],
        "observed_until": observation["expires_at"],
        "status": "offered",
    }


def offers(service, state, graph, gaps):
    if state["offline"]:
        return
    catalog, observation = capability_observation(service.workspace.root)
    if not any(isinstance(row, dict) and row.get("host_preparation") for row in catalog.values()):
        return
    model, _ = validate_blueprint(graph)
    rules, tasks = graph_requirements(model), graph_task_paths(model)
    for gap in gaps:
        node = gap["location"]
        rule = rules.get(node)
        if gap["kind"] != "capability" or rule is None or rule.portability == "unresolved":
            continue
        setups, potential = [], {}
        for rejected in gap.get("candidate_rejections", []):
            if rejected["code"] != "TOOL_UNAVAILABLE":
                continue
            tool = rejected["tool"]
            record = catalog.get(tool, {})
            if record.get("allowed") is not True or not record.get("host_preparation"):
                continue
            try:
                adapter = ImplementationAdapter.model_validate(record["portable"])
                if adapter.locus != "host_mediated" or (
                    rule.portability == "pinned" and rule.pinned_adapter != adapter.ref
                ):
                    continue
                recipe = SetupRecipe.model_validate(record["host_preparation"])
                setup = _offer(service, state, node, tool, recipe, catalog, observation)
                if not _contract_rejections(
                    adapter, tasks[node], rule, state["offline"], record.get("destination")
                ):
                    setups.append(setup)
                elif rule.portability == "flexible":
                    potential[tool] = setup
            except (FlowError, ValidationError, KeyError, TypeError):
                continue
        if (
            not setups
            and potential
            and not any(candidate["allowed"] for candidate in gap.get("candidates", []))
        ):
            projection = {
                **catalog,
                **{tool: {**catalog[tool], "available": True} for tool in potential},
            }
            operations = list(
                observed_tools(service.workspace.root, projection, include_pending=True)
            )
            plan = search_toolchains(tasks[node], rule, operations)
            needed = {step["tool"] for row in plan["plans"] for step in row["steps"]}
            setups = [potential[tool] for tool in sorted(needed & potential.keys())]
        if setups:
            gap["host_setup_offers"] = setups
            gap["actions"].append("authorize_host_preparation")


def _offer(service, state, node, tool, recipe, catalog, observation):
    recipe_hash = digest(recipe.model_dump(mode="json"))
    history = [
        op
        for op in state.get("host_operations", {}).values()
        if op["scope"]["draft_id"] == state["draft_id"]
        and op["scope"]["node"] == node
        and op["scope"]["target"]["tool"] == tool
        and op["scope"]["recipe_hash"] == recipe_hash
        and op["scope"]["action"] == "configure"
    ]
    replaced = {op["scope"].get("replaces") for op in history}
    latest = next((op for op in history if op["operation_id"] not in replaced), None)
    previous = latest["scope"].get("replaces") if latest else None
    if latest and (
        latest["status"] == "compensated"
        or latest["status"] == "verified"
        and latest["receipt"]["outcome"] == "not_executed"
    ):
        previous = latest["operation_id"]
    return _scope(service, state, node, tool, recipe, catalog, observation, replaces=previous)


def append_waiting(state, gaps, make_gap):
    for op in state.get("host_operations", {}).values():
        if (
            op["status"] in PENDING
            or op["status"] == "authorized"
            and op["scope"]["action"] == "compensate"
        ):
            gaps.append(
                make_gap(
                    "host_preparation",
                    "HOST_PREPARATION_PENDING",
                    op["operation_id"],
                    "宿主配置有待處理，已保存操作識別及授權範圍。",
                    ["reconcile_host_preparation"]
                    if op["status"] in PENDING
                    else ["claim_host_preparation"],
                    operation_status=op["status"],
                )
            )


def _fence(service, op):
    runtime = service.workspace.runtime
    scope = op["scope"]
    require(scope["target_hash"] == digest(scope["target"])
            and scope["destination"] == scope["target"]["destination"], "宿主配置的目標指紋不符。")
    endpoint = digest(["host-setup-endpoint", scope["destination"]])
    path = runtime.home / "host_preparation_fences" / (endpoint + ".json")
    reject_links(path)
    return runtime.lock("host-setup:" + endpoint, timeout=10), path


def _result_path(service, op):
    require(op["operation_id"] == digest(op["scope"]) == op["scope_hash"], "宿主配置識別不符。")
    path = (
        service.workspace.runtime.home / "host_preparation_results" / (op["operation_id"] + ".json")
    )
    reject_links(path)
    return path


def _finished_outcome(path, op):
    if not path.is_file():
        return None
    data = read_json(path)
    result = data.get("outcome") if isinstance(data, dict) else None
    require(
        isinstance(result, dict)
        and result.get("status") == "verified"
        and data.get("operation_id") == op["operation_id"]
        and data.get("sha256") == digest([op["scope_hash"], result]),
        "已查證的配置收據指紋不符。",
    )
    return result


def _live_scope(service, state, op):
    scope = op["scope"]
    catalog, observation = capability_observation(service.workspace.root)
    target = catalog.get(scope["target"]["tool"], {})
    require(target.get("allowed") is True, "服務目前缺少可信的授權觀測。")
    recipe = SetupRecipe.model_validate(target.get("host_preparation"))
    original = state.get("host_operations", {}).get(scope["compensates"])
    return _scope(
        service,
        state,
        scope["node"],
        scope["target"]["tool"],
        recipe,
        catalog,
        observation,
        original=original,
        replaces=scope.get("replaces"),
    )


def _claim(service, state, op):
    approval = op.get("approval") or {}
    require(
        op["status"] == "authorized"
        and approval.get("scope_hash") == op["scope_hash"]
        and datetime.fromisoformat(approval["expires_at"]) > datetime.now(UTC),
        "配置授權失效。",
    )
    current = _live_scope(service, state, op)
    require(current["scope_hash"] == op["scope_hash"], "配置觀測或授權範圍已變更。")
    lock, path = _fence(service, op)
    with lock:
        finished = _result_path(service, op)
        outcome = _finished_outcome(finished, op)
        if outcome is not None:
            return {**op, **outcome}, None
        owner = read_json(path) if path.exists() else {}
        if owner.get("operation_id") == op["operation_id"]:
            return {**op, "status": "claimed"}, None
        require(
            not owner or owner["operation_id"] == op["scope"]["compensates"],
            "此服務已有待查操作，請查核原操作 ID。",
        )
        atomic_json(path, {"operation_id": op["operation_id"], "scope_hash": op["scope_hash"]})
    return {**op, "status": "claimed", "observation_id": current["observation_id"]}, {
        "operation_id": op["operation_id"],
        "scope_hash": op["scope_hash"],
        **op["scope"],
    }


def _reconcile(service, op):
    lock, path = _fence(service, op)
    with lock:
        finished = _result_path(service, op)
        owner = read_json(path) if path.exists() else {}
        outcome = _finished_outcome(finished, op)
        if outcome is not None:
            if owner.get("operation_id") == op["operation_id"]:
                path.unlink()
            return {**op, **outcome}
        require(owner.get("operation_id") == op["operation_id"], "請先領取同一配置操作。")
        receipt = read_observation(service.workspace.root, op["operation_id"], op["scope_hash"])
        if receipt is None:
            return {**op, "status": "claimed"}
        scope = op["scope"]
        usage, outputs = receipt.get("usage") or {}, receipt.get("outputs")
        cost = usage.get("cost_micro_usd")
        require(
            type(cost) is int
            and 0 <= cost <= scope["cost_micro_usd"]
            and usage.get("model_calls") == 0,
            "配置回執的費用或用量超出授權。",
        )
        require(
            isinstance(outputs, dict) and outputs.get("target_hash") == scope["target_hash"],
            "回執的服務目標與配置範圍不符。",
        )
        configuration = outputs.get("configuration_fingerprint")
        require(_valid_fingerprint(configuration), "配置回執的設定指紋無效。")
        evidence = {
            "receipt_hash": digest(receipt),
            "outcome": receipt["outcome"],
            "configuration_fingerprint": configuration,
        }
        if receipt["outcome"] == "failed":
            return {**op, "status": "partial", "receipt": evidence}
        catalog, observation = capability_observation(service.workspace.root)
        target = catalog.get(scope["target"]["tool"], {})
        if (
            observation["status"] != "current"
            or observation["id"] == op["observation_id"]
            or any(target.get(key) != value for key, value in scope["target"].items())
            or _fingerprint(target) != configuration
            or receipt["outcome"] == "succeeded"
            and scope["action"] == "configure"
            and target.get("available") is not True
        ):
            return {**op, "status": "receipt_verified", "receipt": evidence}
        if receipt["outcome"] == "not_executed":
            require(configuration == scope["prior_configuration"], "未執行的配置回執與原狀態不同。")
        elif scope["action"] == "configure":
            require(configuration is not None, "配置回執缺少實際設定指紋。")
        else:
            require(
                configuration == scope["restore_configuration"], "補償結果與已確認的回復目標不同。"
            )
        outcome = {"status": "verified", "receipt": evidence}
        atomic_json(
            finished,
            {
                "operation_id": op["operation_id"],
                "outcome": outcome,
                "sha256": digest([op["scope_hash"], outcome]),
            },
        )
        path.unlink()
    return {**op, **outcome}


def submit(service, current, payload):
    from .completion import _preflight

    action, identifier = payload["action"], payload.get("operation_id")
    require(
        isinstance(identifier, str) and bool(re.fullmatch(r"[0-9a-f]{64}", identifier)),
        "宿主配置操作識別無效。",
    )
    operations = dict(current.get("host_operations", {}))
    op = operations.get(identifier)
    dispatch = None
    if action == "authorize_host_preparation":
        live = _preflight(service, current)
        if op is None:
            op = next(
                (
                    item
                    for gap in live["gaps"]
                    for item in gap.get("host_setup_offers", [])
                    if item["operation_id"] == identifier
                ),
                None,
            )
        require(
            op is not None
            and op["status"] == "offered"
            and op["scope_hash"] == payload.get("scope_hash"),
            "請確認目前的具體配置範圍。",
        )
        user_text = payload.get("user_text")
        require(
            isinstance(user_text, str) and 0 < len(user_text.strip()) <= 4000,
            "請轉交實際使用者的配置授權。",
        )
        approved = _live_scope(service, current, op)
        require(approved["scope_hash"] == op["scope_hash"], "配置觀測或授權範圍已變更。")
        op = {
            **op,
            "status": "authorized",
            "approval": {
                "scope_hash": op["scope_hash"],
                "user_text": user_text,
                "expires_at": approved["observed_until"],
            },
        }
    else:
        require(
            op is not None and op["scope_hash"] == payload.get("scope_hash"),
            "宿主配置操作或範圍不符。",
        )
        if action == "claim_host_preparation":
            op, dispatch = _claim(service, current, op)
        elif action == "reconcile_host_preparation":
            if op["status"] != "verified":
                op = _reconcile(service, op)
            if (
                op["status"] == "verified"
                and op["scope"]["compensates"]
                and op["receipt"]["outcome"] == "succeeded"
            ):
                original_id = op["scope"]["compensates"]
                operations[original_id] = {**operations[original_id], "status": "compensated"}
        else:
            require(
                op["scope"]["action"] == "configure"
                and op["status"] in {"verified", "partial"}
                and op["receipt"]["outcome"] != "not_executed",
                "配置沒有可補償的已知結果。",
            )
            catalog, observation = capability_observation(service.workspace.root)
            tool = op["scope"]["target"]["tool"]
            target = catalog.get(tool, {})
            require(target.get("allowed") is True, "目標服務缺少可信的觀測。")
            recipe = SetupRecipe.model_validate(target.get("host_preparation"))
            require(
                digest(recipe.model_dump(mode="json")) == op["scope"]["recipe_hash"],
                "補償配方已改變。",
            )
            op = _scope(
                service,
                current,
                op["scope"]["node"],
                tool,
                recipe,
                catalog,
                observation,
                original=op,
            )
            op = operations.get(op["operation_id"], op)
    operations[op["operation_id"]] = op
    updated = _preflight(service, {**current, "host_operations": operations})
    if current["status"] in {"paused", "cancelled", "archived", "installed"}:
        updated["status"] = current["status"]
    return {**updated, **({"_host_dispatch": dispatch} if dispatch is not None else {})}
