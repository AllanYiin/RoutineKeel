"""Operator-side execution of one authorized Graph task over configured MCP stdio."""

import asyncio
import os
from contextlib import asynccontextmanager

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError
from referencing import Registry

from ..blueprints.source import check_schema, require
from ..graph.capabilities import _host_path, live_tool
from ..graph.external import record_observation
from ..graph.session import checked_value
from ..runtime import FlowError, digest
from .host_inventory import observe_source, read_source_configuration


def _validator(schema):
    require(isinstance(schema, dict), "工具須提供可驗證的結構化合約。")
    check_schema(schema)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise FlowError("INVALID_CONFIGURATION", "工具的結構化合約無效。") from exc
    return Draft202012Validator(schema, registry=Registry(), format_checker=FormatChecker())


def _reconcile(service, session_id, task_id):
    view = service.blueprint_task(session_id, "status")
    if any(task["id"] == task_id and task.get("claimed") for task in view.get("pending_tasks", [])):
        return service.blueprint_task(session_id, "reconcile", {"revision": view["revision"], "task_id": task_id})
    return view


def pending_dispatch(service, session_id, task_id):
    view = service.blueprint_task(session_id, "status")
    task = next((item for item in view.get("pending_tasks", []) if item["id"] == task_id), None)
    if task is None and view["status"] in {"complete", "failed", "cancelled"}:
        return view, None
    require(task is not None and task["kind"] in {"host", "external"}, "請指定已可領取的宿主工具待辦。")
    if task.get("claimed"):
        return _reconcile(service, session_id, task_id), None
    return view, task


async def dispatch_host(service, session_id, task_id, *, on_delta=None):
    view, task = pending_dispatch(service, session_id, task_id)
    if task is None:
        return view
    if task["scope"].get("model_profile"):
        from .host_model import dispatch_model

        return await asyncio.to_thread(dispatch_model, service, session_id, view, task, on_delta=on_delta)
    return await dispatch_mcp(service, session_id, task_id)


async def dispatch_mcp(service, session_id, task_id):
    """Claim once, store the actual provider response, then reconcile the same operation."""
    workspace = service.workspace.root
    view, task = pending_dispatch(service, session_id, task_id)
    if task is None:
        return view

    scope = task["scope"]
    resources = scope.get("resources") or {}
    require(not (resources.get("model_calls", 0) or resources.get("cost_micro_usd", 0)),
            "模型或付費工具需要具實際用量回執的宿主執行器。")
    record = live_tool(workspace, scope["tool"], scope["version"])
    dispatched = False
    try:
        async with _mcp_call(workspace, scope, record, task["inputs"], scope.get("parameters", {})) as send:
            claimed = service.blueprint_task(session_id, "claim", {"revision": view["revision"], "task_id": task_id})
            dispatch = claimed.get("dispatch")
            if dispatch is None:
                return claimed
            dispatched = True
            require(
                dispatch["scope_hash"] == task["scope_hash"]
                and dispatch["provider_tool"] == scope["provider_tool"]
                and dispatch["arguments"] == task["inputs"]
                and dispatch.get("parameters", {}) == scope.get("parameters", {}),
                "MCP 領取回覆與目前範圍不符。",
            )
            outcome, outputs, receipt = await send(dispatch["idempotency_key"])
            record_observation(
                workspace, dispatch["idempotency_key"], dispatch["scope_hash"],
                receipt, outcome, outputs,
            )
    except FlowError:
        raise
    except Exception as exc:
        if not dispatched:
            raise FlowError("CAPABILITY_UNAVAILABLE", "MCP 連線中斷，尚未領取原操作。") from exc
    return _reconcile(service, session_id, task_id)


@asynccontextmanager
async def _mcp_call(workspace, binding, record, arguments, parameters):
    source_id, provider_tool = binding.get("source_id"), binding.get("provider_tool")
    configuration = _host_path(workspace, os.environ.get("ROUTINEKEEL_HOST_SOURCES"))
    require(configuration is not None and source_id and provider_tool
            and record.get("source_id") == source_id and record.get("provider_tool") == provider_tool,
            "此待辦須來自已配置且固定來源的宿主 MCP stdio 工具。")
    try:
        config = read_source_configuration(configuration)
        source = config["sources"][source_id]
        from mcp import Client, StdioServerParameters
    except (OSError, TypeError, ValueError, KeyError, ImportError) as exc:
        raise FlowError("CAPABILITY_UNAVAILABLE", "無法開啟已配置的 MCP 來源。") from exc
    if parameters:
        require("parameters" not in arguments, "工具輸入與保留的 parameters 欄位重疊。")
        arguments = {**arguments, "parameters": parameters}
    require(_validator(record.get("input_schema")).is_valid(arguments), "派送資料與 MCP 輸入合約不符。")
    output_validator = _validator(record.get("output_schema") or record.get("result_schema"))
    ports = record["outputs"]
    mapping = record.get("output_mapping", {name: name for name in ports})
    require(isinstance(mapping, dict) and mapping.keys() == ports.keys()
            and all(isinstance(key, str) and key for key in mapping.values()), "MCP 工具的具名輸出映射無效。")
    launch = StdioServerParameters(command=source["command"], args=source.get("args", []), cwd=configuration.parent)
    async with asyncio.timeout(40), Client(launch, read_timeout_seconds=25) as client:
        live = await observe_source(client, source_id, source)
        observed = live["tools"].get(binding["tool"], {})
        contract = {key: value for key, value in record.items() if key != "portable_qualification"}
        require({key: value for key, value in observed.items() if key != "portable_qualification"} == contract
                and config == read_source_configuration(configuration),
                "MCP 工具的設定、版本或合約已改變，請先重驗綁定。")

        async def send(operation_key):
            # A single session-level call also avoids the SDK's automatic retries.
            result = await client.session.call_tool(
                provider_tool, arguments, read_timeout_seconds=25,
                meta={"routinekeel/idempotencyKey": operation_key},
            )
            outputs, outcome = {}, "failed"
            structured = result.structured_content
            if not result.is_error and isinstance(structured, dict) and output_validator.is_valid(structured):
                try:
                    outputs = {name: checked_value(structured[key], ports[name]) for name, key in mapping.items()}
                    outcome = "succeeded"
                except (FlowError, KeyError):
                    pass
            return outcome, outputs, digest([record["receipt_id"], operation_key, result.model_dump(mode="json")])

        yield send


async def invoke_model_tool_mcp(workspace, target, arguments, operation_key):
    """Send once within an already reserved, read-only ToolCalling operation."""
    from routinekeel_contracts.graph import Task

    from ..graph.capabilities import resolve_task

    binding = target["binding"]
    node = Task.model_validate(target["node"]).model_copy(update={
        "capability": binding["tool"], "capability_version": binding["version"],
    })
    record = live_tool(workspace, binding["tool"], binding["version"], offline=True)
    current = resolve_task(node, workspace, offline=True, catalog={binding["tool"]: record})
    resources = current.get("resources") or {}
    require(current["fingerprint"] == binding["fingerprint"] and current["side_effect"] == "none"
            and not current["permissions"] and not resources.get("model_calls") and not resources.get("cost_micro_usd"),
            "模型工具的 MCP 綁定或授權已變更，請先重驗。")
    attempted = False
    observed = None
    try:
        async with _mcp_call(workspace, binding, record, arguments, node.parameters) as send:
            attempted = True
            observed = await send(operation_key)
    except Exception as exc:
        if observed is None:
            raise FlowError(
                "TOOL_RESULT_UNKNOWN" if attempted else "CAPABILITY_UNAVAILABLE",
                "模型工具的 MCP 執行結果不明，須查原回執。" if attempted else "模型工具的 MCP 來源驗證失敗。",
            ) from exc
    return observed
