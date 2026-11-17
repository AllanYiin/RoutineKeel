from ..contracts import StudioError
from ..registry.builtin import builtin_tools


def tool_support(tool):
    candidates = [item for item in builtin_tools() if item.implementation == tool.implementation]
    if not candidates:
        return False, "目前沒有對應的受控編譯實作。"
    for builtin in candidates:
        if (
            tool.inputs == builtin.inputs
            and tool.outputs == builtin.outputs
            and tool.kind == builtin.kind
            and tool.side_effects == builtin.side_effects
            and set(builtin.permissions) <= set(tool.permissions)
            and (not builtin.resources.network or tool.resources.network)
            and tool.cost_micro_usd >= builtin.cost_micro_usd
        ):
            return True, "受控實作可支援此契約；個別節點參數仍須驗證。"
    return False, "輸入、輸出、副作用或最低權限宣告與受控實作不一致。"


def check_supported(ir, tools):
    signatures = builtin_tools()
    if len(ir.inputs) != 1 or next(iter(ir.inputs.values())).type != "file":
        raise StudioError("IR_UNSUPPORTED_FEATURE", "此編譯器版本接受單一檔案輸入。")
    nonterminal = {dependency for node in ir.nodes for dependency in node.depends_on}
    for ref in ir.outputs.values():
        if ref.split("//")[1].split("/")[0] in nonterminal:
            raise StudioError("IR_UNSUPPORTED_FEATURE", "Runtime 具名交付須參照終端節點。")
    for node in ir.nodes:
        tool = tools[f"{node.tool}@{node.tool_version}"]
        candidates = [t for t in signatures if t.implementation == tool.implementation]
        if node.kind not in {"deterministic", "composite_branch", "llm"} or not candidates:
            raise StudioError(
                "IR_UNSUPPORTED_FEATURE", "此步驟種類或工具尚無可安全編譯的受控實作。"
            )
        if not tool_support(tool)[0] or node.resources.network != tool.resources.network:
            raise StudioError("CONTRACT_MISMATCH", "工具宣告與受控實作不一致。")
        if set(node.inputs) != {"source"} or set(node.outputs) != {"result"}:
            raise StudioError(
                "IR_UNSUPPORTED_FEATURE", "目前受控 Handler 使用單一 source／result 檔案合約。"
            )
        if node.outputs["result"].path in {"result.json", "artifact-set.json"}:
            raise StudioError("CONTRACT_MISMATCH", "產物路徑與 Runtime 索引保留名稱衝突。")
        if node.retry_policy.max_attempts != 1:
            raise StudioError("IR_UNSUPPORTED_FEATURE", "尚未支援自動多次重試。")
        if tool.implementation == "generated.program":
            from .handlers import validate_handler

            validate_handler(node.parameters)
            if node.outputs["result"].format != tool.outputs["result"].format:
                raise StudioError(
                    "CONTRACT_MISMATCH", "新 Handler 必須明確宣告與工具一致的輸出格式。"
                )
            valid = True
        elif tool.implementation == "builtin.llm":
            from .llm_handler import validate_llm

            validate_llm(node)
            valid = True
        elif tool.implementation == "builtin.branch":
            from .branch_lowering import validate_branch

            validate_branch(node, tools)
            if node.outputs["result"].path == "branch.json":
                raise StudioError("CONTRACT_MISMATCH", "分支紀錄名稱保留給 Runtime。")
            valid = True
        elif tool.implementation == "builtin.normalize":
            valid = not (set(node.parameters) - {"strip", "final_newline"}) and all(
                type(v) is bool for v in node.parameters.values()
            )
        else:
            valid = not node.parameters
        if not valid:
            raise StudioError("CONTRACT_MISMATCH", "Handler 參數不符合受控實作。")
