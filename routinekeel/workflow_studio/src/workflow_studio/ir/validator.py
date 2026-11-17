"""Schema and semantic validation; never loads a handler or invokes a model."""

from pydantic import ValidationError

from ..contracts import StudioError
from .models import Port, ToolSignature, WorkflowIR
from .references import parse_reference


def compatible(actual: Port, expected: Port) -> bool:
    return (
        actual.type == expected.type
        and (not expected.format or actual.format == expected.format)
        and (not expected.schema_id or actual.schema_id == expected.schema_id)
        and (not expected.required or actual.required)
    )


def validate_ir(
    payload: dict, tools: dict[str, ToolSignature], *, source_versions=None, verified_records=()
) -> WorkflowIR:
    try:
        ir = WorkflowIR.model_validate(payload)
    except ValidationError as exc:
        locations = [".".join(str(p) for p in e["loc"]) for e in exc.errors()]
        raise StudioError(
            "IR_SCHEMA_INVALID", "IR 欄位不符合契約：" + ", ".join(locations)
        ) from exc
    nodes = {n.id: n for n in ir.nodes}
    if len(nodes) != len(ir.nodes):
        raise StudioError("IR_SCHEMA_INVALID", "節點 ID 重複。")
    evidence = {e.id: e for e in ir.evidence}
    if len(evidence) != len(ir.evidence):
        raise StudioError("IR_SCHEMA_INVALID", "證據 ID 重複。")
    for item in ir.evidence:
        if (
            source_versions is not None
            and (item.source_id, item.source_version) not in source_versions
        ):
            raise StudioError("EVIDENCE_INSUFFICIENT", "證據參照不存在的來源版本。")
        if (
            item.confidence_label in {"verified", "human_confirmed"}
            and item.verified_by not in verified_records
        ):
            raise StudioError("EVIDENCE_INSUFFICIENT", "找不到可驗證的確認紀錄。")
    for node in ir.nodes:
        if not set(node.depends_on) <= nodes.keys():
            raise StudioError("IR_SCHEMA_INVALID", f"{node.id} 依賴不存在的節點。")
        if not set(node.evidence_refs) <= evidence.keys():
            raise StudioError("EVIDENCE_INSUFFICIENT", f"{node.id} 缺少來源證據。")
    ancestors = {}
    pending = dict(nodes)
    while pending:
        ready = [n for n in pending.values() if set(n.depends_on) <= ancestors.keys()]
        if not ready:
            raise StudioError("IR_SCHEMA_INVALID", "節點存在循環依賴。")
        for node in ready:
            ancestors[node.id] = set(node.depends_on)
            for dependency in node.depends_on:
                ancestors[node.id] |= ancestors[dependency]
            del pending[node.id]

    def resolve(value, node=None, *, allow_self=False, allow_output=False):
        ref = parse_reference(value)
        if ref.scheme == "input" and ref.owner in ir.inputs:
            return ir.inputs[ref.owner]
        if ref.scheme == "secret" and ref.owner in ir.secrets:
            return Port(type="string")
        if ref.scheme == "output" and allow_output and ref.owner in ir.outputs:
            return resolve(ir.outputs[ref.owner])
        if ref.scheme == "artifact" and ref.owner in nodes and ref.port in nodes[ref.owner].outputs:
            if (
                node
                and ref.owner not in ancestors[node.id]
                and not (allow_self and ref.owner == node.id)
            ):
                raise StudioError("IR_SCHEMA_INVALID", f"{node.id} 讀取尚未完成的節點。")
            return nodes[ref.owner].outputs[ref.port]
        raise StudioError("IR_SCHEMA_INVALID", f"找不到邏輯引用：{value}")

    for node in ir.nodes:
        key = f"{node.tool}@{node.tool_version}"
        tool = tools.get(key)
        if tool is None or tool.tool_id != node.tool or tool.version != node.tool_version:
            raise StudioError("TOOL_NOT_ALLOWED", f"沒有可用工具版本：{key}")
        if node.kind != tool.kind or node.side_effects != tool.side_effects:
            raise StudioError("CONTRACT_MISMATCH", f"{node.id} 的執行種類或副作用與工具不符。")
        if not set(node.platform_requirements) <= set(tool.platforms):
            raise StudioError("PLATFORM_UNSUPPORTED", f"{node.id} 工具不支援所需平台。")
        required = {name for name, p in tool.inputs.items() if p.required}
        if not required <= node.inputs.keys() or not node.inputs.keys() <= tool.inputs.keys():
            raise StudioError("CONTRACT_MISMATCH", f"{node.id} 的輸入欄位不符。")
        for name, binding in node.inputs.items():
            if not compatible(resolve(binding.source, node), binding.port) or not compatible(
                binding.port, tool.inputs[name]
            ):
                raise StudioError("CONTRACT_MISMATCH", f"{node.id}.{name} 輸入型別不符。")
        if node.outputs.keys() != tool.outputs.keys():
            raise StudioError("CONTRACT_MISMATCH", f"{node.id} 的輸出欄位不符。")
        for name, port in node.outputs.items():
            if not compatible(tool.outputs[name], port):
                raise StudioError("CONTRACT_MISMATCH", f"{node.id}.{name} 輸出型別不符。")
        for condition in node.preconditions:
            resolve(condition.reference, node)
        for condition in node.postconditions:
            resolve(condition.reference, node, allow_self=True)
        checked_outputs = {condition.reference for condition in node.postconditions}
        if not {f"artifact://{node.id}/{name}" for name in node.outputs} <= checked_outputs:
            raise StudioError("IR_SCHEMA_INVALID", f"{node.id} 有未宣告完成檢查的輸出。")
        if node.llm_policy:
            ref = parse_reference(node.llm_policy.api_key)
            if ref.scheme != "secret" or ref.owner not in ir.secrets:
                raise StudioError("IR_SCHEMA_INVALID", "LLM 只能參照已宣告的 secret。")
    if not ir.outputs:
        raise StudioError("IR_SCHEMA_INVALID", "工作流必須宣告交付物。")
    for output in ir.outputs.values():
        if parse_reference(output).scheme != "artifact":
            raise StudioError("IR_SCHEMA_INVALID", "交付物必須參照節點產物。")
        resolve(output)
    return ir
