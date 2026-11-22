"""Resolve tools against local implementations or a host-owned capability inventory."""

import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry
from referencing.exceptions import Unresolvable
from routinekeel_contracts.graph import ForEach, GraphPort, Repeat, Task
from routinekeel_contracts.graph_validator import compatible, validate_blueprint

from ..runtime import FlowError, digest

BUILTINS = {"text.copy": "string", "text.upper": "string", "json.identity": "json"}


def unavailable(message):
    raise FlowError("CAPABILITY_UNAVAILABLE", message)


def read_host_inventory(workspace):
    # This path is launcher configuration. It is never accepted from a blueprint or tool argument.
    configured = os.environ.get("ROUTINEKEEL_HOST_CATALOG")
    if not configured:
        return None
    path = Path(configured)
    if not path.is_absolute() or path.resolve().is_relative_to(Path(workspace).resolve()):
        unavailable("宿主能力目錄須來自資料工作區外的宿主設定。")
    try:
        if path.stat().st_size > 2 * 1024**2:
            unavailable("宿主能力目錄超過大小限制。")
        data = json.loads(path.read_bytes())
        if data["version"] != 1 or not isinstance(data["tools"], dict):
            unavailable("宿主能力目錄格式無效。")
        expires = datetime.fromisoformat(data["expires_at"])
        if expires.tzinfo is None or expires <= datetime.now(UTC):
            unavailable("工具清冊已過期，請由宿主重新取得。")
        return data
    except (OSError, TypeError, KeyError, ValueError):
        unavailable("無法讀取有效的宿主能力目錄。")


def host_catalog(workspace):
    data = read_host_inventory(workspace)
    return data["tools"] if data else {}


def plan_probe_catalog(workspace, task_id, destination):
    """Pass public tool descriptors, without revealing the host's receipt directory."""
    import zipfile

    from ..runtime import atomic_json

    try:
        task = workspace.task(task_id, read_only=True)
        module = task.manifest.python_module
        if not module.startswith("rk_bp_"):
            return {}
        with zipfile.ZipFile(task.folder / task.manifest.package) as wheel:
            item = wheel.getinfo(module + "/tool-signatures.json")
            if item.file_size > 2 * 1024**2:
                return {}
            registry = json.loads(wheel.read(item))
        names = {
            b["tool"]
            for b in registry.get("bindings", {}).values()
            if b.get("provider") == "desktop_host"
        }
        if not names:
            return {}
        source = read_host_inventory(workspace.root)
    except (FlowError, ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile):
        return {}
    if not source:
        return {}
    allowed = {
        "tool",
        "version",
        "receipt_id",
        "available",
        "allowed",
        "platforms",
        "permissions",
        "side_effect",
        "network",
        "inputs",
        "outputs",
        "parameters_schema",
        "resources",
        "kind",
        "distribution",
        "destination",
    }
    tools = {
        name: {k: v for k, v in row.items() if k in allowed}
        for name, row in source["tools"].items()
        if name in names and isinstance(row, dict)
    }
    atomic_json(destination, {"version": 1, "expires_at": source["expires_at"], "tools": tools})
    return {"ROUTINEKEEL_HOST_CATALOG": str(destination)}


def live_tool(workspace, tool, version, offline=False):
    record = host_catalog(workspace).get(tool)
    if (
        not isinstance(record, dict)
        or record.get("version") != version
        or not record.get("receipt_id")
    ):
        unavailable("工具未提供目前版本的宿主回執：" + tool)
    if sys.platform not in record.get("platforms", []) or record.get("available") is not True:
        unavailable("此平台無法使用指定工具：" + tool)
    if record.get("allowed") is not True or (offline and record.get("network", True)):
        raise FlowError("PERMISSION_DENIED", "工具目前未獲准使用，或不符合離線條件。")
    return record


def validate_bindings(workspace, bindings, *, offline=False):
    observed = {}
    for key, b in bindings.items():
        if b.get("kind") == "local_probe":
            found = {}
            for executable in b.get("executables", []):
                if (
                    not executable
                    or Path(executable).name != executable
                    or not shutil.which(executable)
                ):
                    unavailable("缺少宣告的本機工具：" + executable)
                path = Path(shutil.which(executable)).resolve()
                stat = path.stat()
                found[executable] = digest([str(path), stat.st_size, stat.st_mtime_ns])
            if not found:
                unavailable("本機探測需要可查核的工具名稱。")
            observed[key] = {
                "provider": "local_probe",
                "executables": found,
                "binding_hash": digest(b),
            }
            continue
        if b.get("kind") != "host_probe":
            continue
        record = live_tool(workspace, b["tool"], b["version"], offline)
        probe = b.get("probe") or {}
        if (
            record["receipt_id"] != probe.get("request_id")
            or record.get("observed") != probe.get("observed")
            or record.get("input_schema") != b.get("input_schema")
            or record.get("output_schema") != b.get("output_schema")
        ):
            unavailable("草稿宣告與宿主實際觀測不一致：" + key)
        if b.get("authority_hash") and b["authority_hash"] != digest(record):
            unavailable("安裝後的工具或授權內容已變更：" + key)
        observed[key] = {
            "provider": "desktop_host",
            "tool": b["tool"],
            "version": b["version"],
            "contract_hash": digest(record),
            "binding_hash": digest(b),
        }
    return observed


def resolve_task(node, workspace, *, offline=False):
    if node.side_effect == "unresolved":
        unavailable("請先釐清此能力的副作用。")
    if node.capability in BUILTINS:
        if node.parameters:
            raise FlowError("GRAPH_UNSUPPORTED", "此內建工具只接受已宣告的輸入埠。")
        if node.capability_version != "1" or node.permissions or node.side_effect != "none":
            unavailable("內建工具版本、權限或副作用宣告不符。")
        if len(node.inputs) != 1 or len(node.outputs) != 1:
            unavailable("此內建工具需要單一輸入與輸出。")
        source = next(iter(node.inputs.values())).port
        target = next(iter(node.outputs.values()))
        expected = GraphPort(type=BUILTINS[node.capability])
        if not compatible(source, expected) or not compatible(source, target):
            unavailable("內建工具與節點埠型別不符。")
        description = {
            "provider": "builtin",
            "tool": node.capability,
            "version": "1",
            "inputs": {k: v.port.model_dump() for k, v in node.inputs.items()},
            "outputs": {k: v.model_dump() for k, v in node.outputs.items()},
            "permissions": [],
            "side_effect": "none",
            "network": False,
            "platforms": [sys.platform],
            "resources": {"network": False, "model_calls": 0, "cost_micro_usd": 0},
        }
    else:
        record = live_tool(workspace, node.capability, node.capability_version, offline)
        if (
            set(record.get("permissions", [])) != set(node.permissions)
            or record.get("side_effect") != node.side_effect
        ):
            raise FlowError("PERMISSION_DENIED", "工具的必要權限或副作用與藍圖不符。")
        if record.get("inputs") != {
            k: v.port.model_dump(exclude_none=True) for k, v in node.inputs.items()
        } or record.get("outputs") != {
            k: v.model_dump(exclude_none=True) for k, v in node.outputs.items()
        }:
            unavailable("宿主能力輸入或輸出契約不符。")
        description = {
            key: record.get(key)
            for key in (
                "version",
                "platforms",
                "permissions",
                "side_effect",
                "network",
                "inputs",
                "outputs",
                "resources",
                "receipt_id",
                "destination",
            )
        }
        description.update(provider="desktop_host", tool=node.capability)
        schema = record.get("parameters_schema")
        if node.parameters or schema is not None:
            if not isinstance(schema, dict) or schema.get("type") != "object":
                unavailable("工具未宣告節點參數契約。")
            try:
                Draft202012Validator.check_schema(schema)
                valid = Draft202012Validator(schema, registry=Registry()).is_valid(node.parameters)
            except (SchemaError, Unresolvable, ValueError, TypeError, RecursionError):
                unavailable("宿主的節點參數契約無效。")
            if not valid:
                unavailable("節點參數與宿主工具契約不符。")
            description["parameters_schema"] = schema
        if record.get("kind") == "native":
            from ..blueprints.recipe import local_operations

            operation = local_operations().get(node.capability)
            if (
                not operation
                or operation.dist.name != record.get("distribution")
                or operation.dist.version != node.capability_version
            ):
                unavailable("本機安裝的原生元件與受信清冊不符。")
            description.update(provider="native", distribution=operation.dist.name)
    return {**description, "fingerprint": digest(description)}


def resolve_graph(payload, workspace, *, offline=False):
    graph, _ = validate_blueprint(payload)
    resolved, gaps = {}, []

    def visit(body, prefix=""):
        for node in body.nodes:
            key = prefix + node.id
            if isinstance(node, Task):
                try:
                    resolved[key] = resolve_task(node, workspace, offline=offline)
                except FlowError as exc:
                    gaps.append(
                        {"node": key, "tool": node.capability, "code": exc.code, "reason": str(exc)}
                    )
            elif isinstance(node, (Repeat, ForEach)):
                visit(node.body, key + "/")

    visit(graph)
    return {
        "status": "blocked" if gaps else "ready",
        "bindings": resolved,
        "gaps": gaps,
        "fingerprint": digest(
            {"graph": graph.model_dump(mode="json"), "bindings": resolved, "gaps": gaps}
        ),
    }
