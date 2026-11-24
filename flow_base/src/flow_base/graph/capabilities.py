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
from routinekeel_contracts.base_steps import MODEL_STEPS
from routinekeel_contracts.graph import ForEach, GraphPort, Repeat, Task
from routinekeel_contracts.graph_validator import compatible, validate_blueprint

from ..runtime import FlowError, digest
from .base_steps import LOCAL_STEPS

BUILTINS = {"text.copy": "string", "text.upper": "string", "json.identity": "json"}


def unavailable(message, **details):
    raise FlowError("CAPABILITY_UNAVAILABLE", message, **details)


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
            unavailable("工具清冊已過期，請由宿主重新取得。", observation_status="expired")
        return data
    except (OSError, TypeError, KeyError, ValueError):
        unavailable("無法讀取有效的宿主能力目錄。")


def host_catalog(workspace):
    data = read_host_inventory(workspace)
    return data["tools"] if data else {}


def capability_observation(workspace):
    try:
        data = read_host_inventory(workspace)
        snapshot_id = digest([sys.platform, data])
    except FlowError as error:
        status = error.details.get("observation_status", "invalid")
        return {}, {"id": digest([sys.platform, status]), "status": status}
    except (TypeError, ValueError):
        return {}, {"id": digest([sys.platform, "invalid"]), "status": "invalid"}
    if data is None:
        return {}, {"id": digest([sys.platform, "disconnected"]), "status": "disconnected"}
    return data["tools"], {
        "id": snapshot_id,
        "status": "current",
        "observed_at": data.get("observed_at"),
        "expires_at": data["expires_at"],
    }


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
        bindings = registry.get("bindings", {}).values()
        leaves = [
            leaf for binding in bindings
            for leaf in (binding["chain_bindings"].values() if binding.get("provider") == "toolchain" else [binding])
        ]
        names = {
            b["tool"]
            for b in leaves
            if b.get("provider") in {"desktop_host", "portable_local"}
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
        "portable",
        "scope_hash",
        "source_id",
        "provider_tool",
        "preparation",
    }
    tools = {
        name: {k: v for k, v in row.items() if k in allowed}
        for name, row in source["tools"].items()
        if name in names and isinstance(row, dict)
    }
    atomic_json(destination, {"version": 1, "expires_at": source["expires_at"], "tools": tools})
    return {"ROUTINEKEEL_HOST_CATALOG": str(destination)}


def live_tool(workspace, tool, version, offline=False, *, catalog=None):
    record = (host_catalog(workspace) if catalog is None else catalog).get(tool)
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


def resolve_task(node, workspace, *, offline=False, catalog=None):
    if node.side_effect == "unresolved":
        unavailable("請先釐清此能力的副作用。")
    if node.capability in LOCAL_STEPS:
        description = {
            "provider": "builtin", "tool": node.capability, "version": "1",
            "inputs": {k: v.port.model_dump() for k, v in node.inputs.items()},
            "outputs": {k: v.model_dump() for k, v in node.outputs.items()},
            "permissions": [], "side_effect": "none", "network": False, "platforms": [sys.platform],
            "resources": {"network": False, "model_calls": 0, "cost_micro_usd": 0},
        }
    elif node.capability in BUILTINS:
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
        record = live_tool(workspace, node.capability, node.capability_version, offline, catalog=catalog)
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
        for key in ("source_id", "provider_tool"):
            if key in record:
                description[key] = record[key]
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
        if node.capability in MODEL_STEPS:
            resources = description.get("resources") or {}
            if (description["provider"] != "desktop_host"
                or type(resources.get("model_calls")) is not int or resources["model_calls"] < 1
                or type(resources.get("cost_micro_usd")) is not int or resources["cost_micro_usd"] < 0
                or (record.get("network", True) and node.side_effect != "external")):
                unavailable("模型步驟須綁定有模型呼叫、成本上限與相符授權的宿主能力。")
    return {**description, "fingerprint": digest(description)}


def resolve_graph(payload, workspace, *, offline=False, admission=None, allow_waiting=False, planning_search=None):
    from .locking import read_lock
    from .portable import graph_requirements, resolve_requirement
    from .qualification import graph_fingerprint

    graph, _ = validate_blueprint(payload)
    reviewed_tools = graph.provenance.get("design_review", {}).get("details", {}).get("tools", {})
    requirements = graph_requirements(graph)
    observation = capability_observation(workspace) if requirements else None
    resolved, gaps, decisions = {}, [], []
    locked = None
    lock_error = False
    if requirements:
        hashes = {
            binding["lock_hash"] for binding in (admission or {}).values() if "lock_hash" in binding
        }
        try:
            if len(hashes) > 1:
                raise FlowError("BINDING_STALE", "安裝包包含不同的環境鎖定。")
            authority = read_lock(
                workspace, graph_fingerprint(graph), lock_hash=next(iter(hashes), None)
            )
            if authority:
                locked = authority[0]
        except FlowError:
            lock_error = True

    def check_locus(key, actual):
        expected = reviewed_tools.get(key, {}).get("locus")
        if expected and actual != expected:
            raise FlowError("CONTRACT_MISMATCH", f"工具執行位置與定稿不同：{key}（{expected} → {actual}）")

    def visit(body, prefix=""):
        for node in body.nodes:
            key = prefix + node.id
            if isinstance(node, Task):
                try:
                    rule = requirements.get(key)
                    if rule is not None and node.capability in LOCAL_STEPS | MODEL_STEPS:
                        unavailable("底座通用步驟使用其固定的能力契約。")
                    if rule is None:
                        binding = resolve_task(node, workspace, offline=offline)
                        locus = "host_mediated" if binding["provider"] == "desktop_host" else "local_runtime"
                        check_locus(key, locus)
                        resolved[key] = binding
                    else:
                        selected = locked.bindings.get(key) if locked else None
                        resolution = {
                            "offline": offline,
                            "observation": observation,
                            "workflow": graph.workflow.model_dump(mode="json"),
                            "planning_search": planning_search,
                        }
                        decision, candidates = resolve_requirement(
                            key, node, rule, workspace,
                            selected_plan=selected.toolchain_plan if selected else None,
                            **resolution,
                        )
                        live = candidates.get(selected.adapter) if selected else None
                        if selected and live and live[0]["allowed"] and live[1] is not None:
                            candidate, concrete = live
                            if all(
                                candidate[field] == getattr(selected, field)
                                for field in (
                                    "environment_fingerprint",
                                    "schema_hash",
                                    "implementation_hash",
                                    "provider_version",
                                    "model_version",
                                    "permission_scope",
                                )
                            ):
                                locked_binding = {
                                    **concrete,
                                    "lock_hash": locked.lock_hash,
                                    "qualification_receipt": selected.qualification_receipt,
                                }
                                check_locus(key, selected.execution_locus)
                                resolved[key] = locked_binding
                                decision.update(
                                    status="bound",
                                    code=None,
                                    installation_allowed=True,
                                    selected_adapter=selected.adapter,
                                )
                                decisions.append(decision)
                                continue
                        if (
                            allow_waiting
                            and selected
                            and selected.toolchain_plan is None
                            and selected.execution_locus == "host_mediated"
                            and admission
                            and key in admission
                            and admission[key].get("lock_hash") == locked.lock_hash
                            and admission[key].get("qualification_receipt")
                            == selected.qualification_receipt
                            and (decision["catalog_unavailable"]
                                 or admission[key]["tool"] not in observation[0])
                        ):
                            check_locus(key, selected.execution_locus)
                            resolved[key] = admission[key]
                            decision.update(
                                status="bound",
                                code="HOST_TOOL_UNAVAILABLE",
                                selected_adapter=selected.adapter,
                            )
                            decisions.append(decision)
                            continue
                        if admission is None and selected and selected.toolchain_plan:
                            stale_planning = decision.get("toolchain_planning", {})
                            decision, _ = resolve_requirement(key, node, rule, workspace, **resolution)
                            planning = decision.get("toolchain_planning")
                            if planning is not None and stale_planning.get("rejections"):
                                planning.setdefault("rejections", []).extend(stale_planning["rejections"])
                        if lock_error or selected:
                            decision.update(status="blocked", code="BINDING_STALE")
                        decisions.append(decision)
                        gaps.append(
                            {
                                "node": key,
                                "tool": rule.capability,
                                "code": decision["code"],
                                "reason": "請依目前環境資訊完成能力選擇與資格驗證。",
                            }
                        )
                except FlowError as exc:
                    gaps.append(
                        {"node": key, "tool": node.capability, "code": exc.code, "reason": str(exc)}
                    )
            elif isinstance(node, (Repeat, ForEach)):
                visit(node.body, key + "/")

    visit(graph)
    report = {
        "status": "blocked" if gaps else "ready",
        "bindings": resolved,
        "gaps": gaps,
        "fingerprint": digest(
            {"graph": graph.model_dump(mode="json"), "bindings": resolved, "gaps": gaps}
        ),
    }
    if decisions:
        report["portable_decisions"] = decisions
        report["capability_observation"] = observation[1]
    return report
