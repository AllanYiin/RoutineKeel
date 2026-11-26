"""Scope and meter bounded tool calls against already admitted Graph bindings."""

import asyncio
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field, field_validator
from routinekeel_contracts.domain_assets import canonical_json

from ..blueprints.sessions import refs
from ..blueprints.source import require
from ..outputs import reject_links
from ..runtime import FlowError, atomic_json, digest
from .external import receipt_path


class ToolMode(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tools: dict[str, str] = Field(min_length=1)
    max_rounds: int = Field(default=3, ge=2, le=3)
    max_tool_calls: int = Field(ge=1, le=12)

    @field_validator("tools")
    @classmethod
    def named_tools(cls, value):
        if not all(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", alias)
                   and re.fullmatch(r"[a-z][a-z0-9_]{0,62}", node) for alias, node in value.items()):
            raise ValueError("工具別名與節點識別碼無效")
        return value


def bind_tools(nodes, bindings, model_key, mode):
    """Freeze sibling task contracts before showing the confirmation scope."""
    prefix = model_key.rpartition("/")[0]
    registry = {}
    for alias, node_id in mode["tools"].items():
        key = f"{prefix}/{node_id}" if prefix else node_id
        node, binding = nodes.get(key), bindings.get(key, {})
        resources = binding.get("resources") or {}
        provider = binding.get("provider")
        executable = (
            provider == "builtin" and binding.get("tool") in {"text.upper", "text.copy", "json.identity"}
            or provider == "portable_local" and binding.get("qualification_receipt")
            or provider == "desktop_host" and binding.get("source_id") and binding.get("provider_tool")
        )
        require(
            node is not None and node["kind"] == "task" and key != model_key and executable
            and binding.get("side_effect") == "none" and binding.get("network") is False
            and not binding.get("permissions") and not resources.get("model_calls")
            and not resources.get("cost_micro_usd"),
            "工具模式須引用已綁定、無副作用且符合離線範圍的本機或 MCP 節點：" + node_id,
        )
        registry[alias] = {"node": node, "binding": binding}
    return registry


def _port_schema(port):
    kind = port["type"]
    if kind == "json":
        return {}
    if kind == "file":
        return {"type": "string", "pattern": r"^asset:[a-f0-9]{64}\.[a-z0-9]{1,8}$"}
    schema = {"type": kind}
    if port.get("items") is not None:
        schema["items"] = _port_schema(port["items"])
    return schema


def tool_definitions(registry):
    tools = []
    for alias, entry in registry.items():
        ports = {name: item["port"] for name, item in entry["node"]["inputs"].items()}
        capability = entry["binding"]["tool"]
        description = {
            "text.upper": "Convert the source string to uppercase and return result.",
            "text.copy": "Copy the source string and return result.",
            "json.identity": "Return the supplied JSON value unchanged.",
        }.get(capability, capability)
        tools.append({"type": "function", "function": {"name": alias, "description": description, "parameters": {
            "type": "object", "properties": {name: _port_schema(port) for name, port in ports.items()},
            "required": [name for name, port in ports.items() if port.get("required", True)],
            "additionalProperties": False,
        }}})
    return tools


class ToolRoundJournal:
    """Only the outer Graph claim owner may create and update this operation journal."""

    def __init__(self, workspace, task, mode):
        path = receipt_path(workspace, task["idempotency_key"])
        self.path = path.with_suffix(".tool-rounds.json")
        reject_links(self.path)
        self.workspace = workspace
        self.operation_key = task["idempotency_key"]
        self.registry = task["scope"]["tool_registry"]
        self.asset_refs = set(refs(task["inputs"]))
        self.max_rounds = min(mode["max_rounds"], task["scope"]["resources"]["model_calls"])
        self.max_tool_calls = mode["max_tool_calls"]
        self.state = {"scope_hash": task["scope_hash"], "model_requests": [], "tool_requests": {}}
        require(not self.path.exists(), "此工具循環已有派送紀錄，須依原操作查詢。")

    def _save(self):
        atomic_json(self.path, self.state)

    def begin_model_call(self):
        requests = self.state["model_requests"]
        require(len(requests) < self.max_rounds, "已達模型輪次或呼叫額度。")
        requests.append({"outcome": "unknown"})
        self._save()

    def end_model_call(self, usage):
        self.state["model_requests"][-1] = {"outcome": "observed", "usage": usage}
        self._save()

    def reject_round(self, error):
        self.state["rejection"] = {"code": error.code, "reason": str(error)}
        self._save()

    @property
    def usage(self):
        model_requests = self.state["model_requests"]
        return {"model_calls": len(model_requests), "cost_micro_usd": 0,
                "tool_calls": len(self.state["tool_requests"]),
                **{name: sum(row.get("usage", {}).get(name, 0) for row in model_requests)
                   for name in ("prompt_tokens", "completion_tokens")}}

    def dispatch_tools(self, proposals):
        from jsonschema import Draft202012Validator

        require(0 < len(proposals) <= 4, "單輪最多四個工具提議。")
        require(len(self.state["model_requests"]) < self.max_rounds, "剩餘額度不足以將工具結果回填模型。")
        schemas = {item["function"]["name"]: item["function"]["parameters"]
                   for item in tool_definitions(self.registry)}
        history = self.state["tool_requests"]
        approved, new = {}, {}
        for proposal in proposals:
            ident, name, arguments = proposal["id"], proposal["function"]["name"], proposal["function"]["arguments"]
            require(name in schemas, "模型提出未獲核准的工具。")
            require(Draft202012Validator(schemas[name]).is_valid(arguments), "模型工具參數與合約不符。")
            require(set(refs(arguments)) <= self.asset_refs, "模型工具參數引用未授權的素材。")
            signature = digest([name, arguments])
            existing = history.get(ident) or new.get(ident)
            require(existing is None or signature == existing["signature"], "同一工具 ID 附有不一致的參數。")
            if existing is None:
                new[ident] = {"name": name, "signature": signature, "outcome": "unknown", "arguments": arguments,
                              "operation_key": digest([self.operation_key, ident, signature])}
            approved[ident] = name
        require(len(history) + len(new) <= self.max_tool_calls, "已超出本次工具呼叫額度。")
        require(all(history.get(ident, {}).get("outcome", "succeeded") == "succeeded" for ident in approved),
                "工具已有未確認或失敗的操作，須查原回執。")
        if new:
            history.update(new)
            self._save()
            self._execute(new)
        return [{"role": "tool", "tool_name": name, "tool_call_id": ident,
                 "content": canonical_json(history[ident]["outputs"]).decode("utf-8")}
                for ident, name in approved.items()]

    def _execute(self, entries):
        with ThreadPoolExecutor(max_workers=len(entries)) as pool:
            futures = {}
            for entry in entries.values():
                target = self.registry[entry["name"]]
                futures[pool.submit(self._invoke, target, entry)] = entry
            for future in as_completed(futures):
                entry = futures[future]
                try:
                    outcome, outputs, receipt = future.result()
                    if receipt:
                        entry["provider_receipt"] = receipt
                    if outcome == "succeeded":
                        self.asset_refs.update(refs(outputs))
                    entry.update(outcome=outcome, outputs=outputs, outputs_hash=digest(outputs))
                except Exception as error:
                    code = getattr(error, "code", type(error).__name__)
                    entry.update(outcome="unknown" if code == "TOOL_RESULT_UNKNOWN" else "failed", error=code)
                self._save()
        if any(entry["outcome"] == "unknown" for entry in entries.values()):
            raise FlowError("TOOL_RESULT_UNKNOWN", "MCP 工具已有領取且結果不明，須查原回執。")
        require(all(entry["outcome"] == "succeeded" for entry in entries.values()), "已派送的工具未全部完成。")

    def _invoke(self, target, entry):
        arguments = deepcopy(entry["arguments"])
        if target["binding"]["provider"] == "desktop_host":
            from ..adapters.host_dispatch import invoke_model_tool_mcp

            return asyncio.run(invoke_model_tool_mcp(self.workspace, target, arguments, entry["operation_key"]))
        from .qualification import execute_local

        outputs = execute_local(target["binding"], target["node"], arguments,
                                deepcopy(target["node"].get("parameters", {})), workspace=self.workspace)
        return "succeeded", outputs, None
