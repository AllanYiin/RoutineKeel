"""Common ordering and promotion policy used by both package front ends."""

import hashlib
import json
import re
from graphlib import CycleError, TopologicalSorter

from .errors import StudioError


def layers(dependencies):
    sorter = TopologicalSorter(dependencies)
    try:
        sorter.prepare()
    except CycleError as exc:
        raise StudioError("GRAPH_INVALID", "一般依賴圖不允許循環") from exc
    while sorter.is_active():
        ready = sorted(sorter.get_ready())
        yield ready
        sorter.done(*ready)


def fingerprint(data):
    return hashlib.sha256(
        json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def canonical_graph(ir):
    """Keep the original compatibility IR as evidence while admitting one graph representation."""
    from .graph_validator import validate_blueprint

    if ir.get("blueprint_schema_version") == 2:
        graph, _ = validate_blueprint(ir)
        return graph.model_dump(mode="json", exclude_none=True)
    from .models import WorkflowIR

    legacy = WorkflowIR.model_validate(ir)

    def port(p):
        return p.model_dump(include={"type", "required", "format"}, exclude_none=True)

    nodes = []
    for n in legacy.nodes:
        ordinary = {
            k: {"source": v.source, "port": port(v.port)}
            for k, v in n.inputs.items()
            if v.source.startswith(("input://", "artifact://"))
        }
        nodes.append(
            {
                "id": n.id,
                "kind": "task",
                "capability": n.tool,
                "capability_version": n.tool_version,
                "depends_on": n.depends_on,
                "inputs": ordinary,
                "outputs": {k: port(v) for k, v in n.outputs.items()},
                "permissions": [],
                "side_effect": n.side_effects,
                "parameters": {"compatibility_node": n.model_dump(mode="json")},
                "evidence": n.evidence_refs,
            }
        )
    from .references import parse_reference

    outputs = dict(legacy.outputs)
    used = {p for n in nodes for p in n["depends_on"]}
    used.update(
        parse_reference(b["source"]).owner
        for n in nodes
        for b in n["inputs"].values()
        if b["source"].startswith("artifact://")
    )
    terminals = {n.id for n in legacy.nodes} - used
    if not terminals <= {parse_reference(ref).owner for ref in outputs.values()}:
        by_id = {n.id: n for n in legacy.nodes}
        end = next(
            f"legacy_final_{i}" for i in range(len(nodes) + 1) if f"legacy_final_{i}" not in by_id
        )
        declared = {}
        for name, value in outputs.items():
            ref = parse_reference(value)
            declared[name] = port(by_id[ref.owner].outputs[ref.port])
        nodes.append(
            {
                "id": end,
                "kind": "join",
                "policy": "all_success",
                "depends_on": sorted(terminals),
                "inputs": {k: {"source": v, "port": declared[k]} for k, v in outputs.items()},
                "outputs": declared,
            }
        )
        outputs = {k: f"artifact://{end}/{k}" for k in outputs}
    payload = {
        "blueprint_schema_version": 2,
        "graph_contract_version": "2.1",
        "workflow": {
            "id": legacy.workflow_id,
            "version": f"1.0.{legacy.revision}",
            "revision": legacy.revision,
            "goal": legacy.goal.description,
        },
        "inputs": {k: port(v) for k, v in legacy.inputs.items()},
        "nodes": nodes,
        "outputs": outputs,
        "quality_contract": {"success_criteria": legacy.goal.success_criteria},
        "provenance": {"source_format": "studio-ir/1.0", "source_hash": fingerprint(ir)},
    }
    graph, _ = validate_blueprint(payload)
    return graph.model_dump(mode="json", exclude_none=True)


def issue_admission(ir, package_hash, validation, observations):
    if (
        validation != "PASS"
        or not re.fullmatch("[0-9a-f]{64}", package_hash)
        or not observations
        or any(
            row.get("status") != "passed"
            or not row.get("case_id")
            or not re.fullmatch("[0-9a-f]{64}", str(row.get("output_hash", "")))
            for row in observations
        )
    ):
        raise StudioError("TEST_FAILED", "必要契約與實際試做須全部通過才可取得安裝收據。")
    if len({r["case_id"] for r in observations}) != len(observations):
        raise StudioError("TEST_FAILED", "驗收案例識別碼重複。")
    graph = canonical_graph(ir)
    receipt = {
        "policy": "routinekeel-builder/1",
        "package_hash": package_hash,
        "ir_hash": fingerprint(ir),
        "graph_hash": fingerprint(graph),
        "observations_hash": fingerprint(observations),
        "case_count": len(observations),
    }
    return {**receipt, "receipt_hash": fingerprint(receipt)}
