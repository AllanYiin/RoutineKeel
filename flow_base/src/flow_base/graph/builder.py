"""Build a plan from validated graph symbols and independently resolved contracts."""

from routinekeel_contracts.execution_review import child_execution_review
from routinekeel_contracts.graph_trials import trial_case_gaps
from routinekeel_contracts.graph_validator import validate_blueprint

from ..blueprints.source import require
from ..runtime import FlowError, digest
from .capabilities import resolve_graph


def verify_pattern_admission(graph, workspace):
    provenance = graph["provenance"]
    compositions = provenance.get("pattern_compositions", [])
    if compositions:
        from ..studio import studio_request

        receipt = studio_request(workspace, "verify_pattern_admission", {
            "compositions": compositions, "rehearsal": provenance.get("pattern_rehearsal"),
        })
        require(receipt.get("verified") is True, "缺少目前有效的 Pattern 與 Pack 資格。")


def preflight_graph(graph, workspace, *, offline=False):
    resolved = resolve_graph(graph, workspace, offline=offline)
    cases = graph["quality_contract"].get("golden_cases")
    build_gaps = trial_case_gaps(cases, graph["outputs"])
    admission_failed = False
    try:
        verify_pattern_admission(graph, workspace)
    except FlowError as error:
        build_gaps.append({"code": error.code, "reason": str(error)})
        admission_failed = True
    build_gaps.extend(
        {"code": "GRAPH_UNSUPPORTED", "node": key, "reason": "Graph Builder 尚未支援此原生能力。"}
        for key, binding in resolved["bindings"].items()
        if binding["provider"] == "native"
    )
    return {
        **resolved,
        "status": "blocked" if admission_failed else resolved["status"],
        "build_status": "blocked" if resolved["gaps"] or build_gaps else "ready",
        "build_gaps": build_gaps,
    }


def build_graph(source, requested, cases, workspace, *, attachments=None):
    graph = source["graph"]
    verify_pattern_admission(graph, workspace)
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
    locks = {
        binding["lock_hash"] for binding in resolved["bindings"].values() if "lock_hash" in binding
    }
    if locks:
        from .locking import read_lock
        from .qualification import graph_fingerprint

        shape, _ = validate_blueprint(graph)
        require(len(locks) == 1, "建置僅能使用已確認的單一環境鎖定。")
        authority = read_lock(workspace, graph_fingerprint(shape), lock_hash=next(iter(locks)))
        require(
            authority and authority[0].blueprint_hash == source["source_hash"],
            "環境鎖定屬於另一份藍圖版本，請重新驗證。",
        )
    require(isinstance(required, list) and required, "藍圖須附成功條件與驗收案例")
    if cases is None:
        cases = required
    require(
        isinstance(cases, list) and all(c in cases for c in required), "不可省略已確認的驗收案例"
    )
    assignments = graph["provenance"].get("design_review", {}).get("details", {}).get("skills", {})
    attachments = attachments or {}
    documents = {}
    for skill, paths in assignments.items():
        prefix = f"skills/{skill}/"
        group = {name[len(prefix):]: data.decode("utf-8-sig")
                 for name, data in attachments.items() if name.startswith(prefix)}
        require("SKILL.md" in group, "定稿指定的技能缺少已驗證來源。")
        for path in paths:
            documents.setdefault(path, {})[skill] = group
    plan = compile_plan_tree(graph, source["source_hash"], workspace, resolved, documents=documents)
    return plan, graph, {"bindings": resolved["bindings"]}, cases


def compile_plan_tree(graph, source_hash, workspace, resolved, *, documents=None):
    if any(
        n["kind"] not in {"task", "human_review", "switch", "join", "foreach", "repeat_until"}
        or (
            n["kind"] == "task"
            and resolved["bindings"][n["id"]]["provider"]
            not in {"builtin", "desktop_host", "portable_local"}
        )
        for n in graph["nodes"]
    ):
        raise FlowError("GRAPH_UNSUPPORTED", "此執行版本尚不支援所選控制節點或工具。")
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
    documents = documents or {}
    local_docs = {key: value for key, value in documents.items() if "/" not in key}
    if local_docs:
        plan["skill_documents"] = local_docs
    children = {}
    for node in graph["nodes"]:
        if node["kind"] not in {"foreach", "repeat_until"}:
            continue
        child = {
            **node["body"],
            "blueprint_schema_version": 2,
            "graph_contract_version": graph.get("graph_contract_version", "2.0"),
            "workflow": graph["workflow"],
            "quality_contract": child_execution_review(graph["quality_contract"], node["id"]),
            "provenance": {"parent_graph_hash": plan["graph_hash"], "node": node["id"]},
        }
        model, _ = validate_blueprint(child)
        child = model.model_dump(mode="json", exclude_none=True)
        prefix = node["id"] + "/"
        pinned = {k[len(prefix) :]: v for k, v in bindings.items() if k.startswith(prefix)}
        if any("lock_hash" in binding for binding in pinned.values()):
            child_bindings = {
                "status": "ready",
                "bindings": pinned,
                "fingerprint": digest(
                    {"graph": model.model_dump(mode="json"), "bindings": pinned, "gaps": []}
                ),
            }
        else:
            child_bindings = resolve_graph(child, workspace)
        require(
            child_bindings["status"] == "ready" and child_bindings["bindings"] == pinned,
            "子圖工具版本已變更",
        )
        children[node["id"]] = compile_plan_tree(
            child, source_hash, workspace, child_bindings,
            documents={key[len(prefix):]: value for key, value in documents.items() if key.startswith(prefix)},
        )
    if children:
        plan["children"] = children
    return plan
