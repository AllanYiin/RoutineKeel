"""Build a plan from validated graph symbols and independently resolved contracts."""

from routinekeel_contracts.graph_validator import validate_blueprint

from ..blueprints.source import require
from ..runtime import FlowError, digest
from .capabilities import resolve_graph


def build_graph(source, requested, cases, workspace):
    graph = source["graph"]
    resolved = resolve_graph(graph, workspace)
    require(
        resolved["status"] == "ready",
        "有未綁定的能力：" + str([g["node"] for g in resolved["gaps"]]),
    )
    require(
        requested == {"capability_fingerprint": resolved["fingerprint"]},
        "請先取得目前能力清單並綁定相同指紋",
    )
    required = graph["quality_contract"].get("golden_cases")
    require(isinstance(required, list) and required, "藍圖須附成功條件與驗收案例")
    if cases is None:
        cases = required
    require(
        isinstance(cases, list) and all(c in cases for c in required), "不可省略已確認的驗收案例"
    )
    plan = compile_plan_tree(graph, source["source_hash"], workspace, resolved)
    return plan, graph, {"bindings": resolved["bindings"]}, cases


def compile_plan_tree(graph, source_hash, workspace, resolved):
    if any(
        n["kind"] not in {"task", "human_review", "switch", "join", "foreach", "repeat_until"}
        or (
            n["kind"] == "task"
            and resolved["bindings"][n["id"]]["provider"] not in {"builtin", "desktop_host"}
        )
        for n in graph["nodes"]
    ):
        raise FlowError("GRAPH_UNSUPPORTED", "此執行版本尚不支援所選控制節點或工具。")
    if any(n.get("parameters") for n in graph["nodes"]):
        raise FlowError("GRAPH_UNSUPPORTED", "內建純資料工具只接受已宣告的輸入埠。")
    bindings = resolved["bindings"]
    dependencies = {}
    _, order = validate_blueprint(graph, dependencies=dependencies)
    plan = {
        "format": 2,
        "workflow": graph["workflow"]["id"],
        "version": graph["workflow"]["version"],
        "source_hash": source_hash,
        "graph_hash": digest(graph),
        "graph": graph,
        "capability_fingerprint": resolved["fingerprint"],
        "bindings_hash": digest(bindings),
        "final_outputs": graph["outputs"],
        "order": order,
        "dependencies": dependencies,
    }
    children = {}
    for node in graph["nodes"]:
        if node["kind"] not in {"foreach", "repeat_until"}:
            continue
        child = {
            **node["body"],
            "blueprint_schema_version": 2,
            "graph_contract_version": graph.get("graph_contract_version", "2.0"),
            "workflow": graph["workflow"],
            "quality_contract": {},
            "provenance": {"parent_graph_hash": plan["graph_hash"], "node": node["id"]},
        }
        model, _ = validate_blueprint(child)
        child = model.model_dump(mode="json", exclude_none=True)
        child_bindings = resolve_graph(child, workspace)
        prefix = node["id"] + "/"
        pinned = {k[len(prefix) :]: v for k, v in bindings.items() if k.startswith(prefix)}
        require(
            child_bindings["status"] == "ready" and child_bindings["bindings"] == pinned,
            "子圖工具版本已變更",
        )
        children[node["id"]] = compile_plan_tree(child, source_hash, workspace, child_bindings)
    if children:
        plan["children"] = children
    return plan
