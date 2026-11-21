from pydantic import ValidationError

from ..contracts import StudioError
from ..ir.conditions import BranchPlan
from ..ir.validator import compatible
from ..registry.builtin import builtin_tools


def validate_branch(node, tools):
    try:
        plan = BranchPlan.model_validate(node.parameters)
    except ValidationError as exc:
        raise StudioError("IR_UNSUPPORTED_FEATURE", "分支只支援受控條件及兩條確定性路徑。") from exc
    signatures = []
    builtins = {t.implementation: t for t in builtin_tools()}
    for arm in (plan.when_true, plan.when_false):
        tool = tools.get(f"{arm.tool}@{arm.version}")
        if tool is None or tool.kind != "deterministic" or tool.side_effects == "external":
            raise StudioError("TOOL_NOT_ALLOWED", "分支包含未授權或非確定性工具。")
        if tool.implementation not in {
            "builtin.copy",
            "builtin.normalize",
            "builtin.json",
            "builtin.report",
        }:
            raise StudioError("IR_UNSUPPORTED_FEATURE", "不支援巢狀或任意程式分支。")
        declared = builtins[tool.implementation]
        if (
            tool.inputs != declared.inputs
            or tool.outputs != declared.outputs
            or tool.side_effects != declared.side_effects
        ):
            raise StudioError("CONTRACT_MISMATCH", "分支工具合約與受控實作不一致。")
        if not compatible(node.inputs["source"].port, tool.inputs["source"]):
            raise StudioError("CONTRACT_MISMATCH", "分支輸入與工具不相容。")
        valid = (
            (
                not (set(arm.parameters) - {"strip", "final_newline"})
                and all(type(v) is bool for v in arm.parameters.values())
            )
            if tool.implementation == "builtin.normalize"
            else not arm.parameters
        )
        if not valid:
            raise StudioError("CONTRACT_MISMATCH", "分支工具參數不合法。")
        signatures.append(tool.outputs)
    if signatures[0] != signatures[1]:
        raise StudioError("CONTRACT_MISMATCH", "兩條分支必須具有相同輸出合約。")
    return plan


def lower_branch(node, tools):
    plan = validate_branch(node, tools)
    result = plan.model_dump()
    for name in ("when_true", "when_false"):
        arm = result[name]
        arm["implementation"] = tools[f"{arm['tool']}@{arm['version']}"].implementation
    return result
