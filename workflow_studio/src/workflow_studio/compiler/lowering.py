from ..ir.references import parse_reference
from ..ir.versioning import ir_fingerprint


def lower(ir, tools):
    pending = {node.id: node for node in ir.nodes}
    ordered, seen = [], set()
    while pending:
        ready = sorted(
            (n for n in pending.values() if set(n.depends_on) <= seen), key=lambda n: n.id
        )
        for node in ready:
            ordered.append(node)
            seen.add(node.id)
            del pending[node.id]
    input_id = next(f"studio_input_{i}" for i in range(201) if f"studio_input_{i}" not in seen)
    steps = [
        {
            "id": input_id,
            "handler": "studio.input",
            "needs": [],
            "parameters": {},
            "cacheable": True,
            "side_effects": "local_write",
            "retry_policy": "none",
            "output_artifacts": ["input.bin"],
        }
    ]
    operations = {}
    for node in ordered:
        dependencies = set(node.depends_on)
        refs = [binding.source for binding in node.inputs.values()] + [
            c.reference for c in node.preconditions + node.postconditions
        ]
        for ref in refs:
            parsed = parse_reference(ref)
            if parsed.scheme == "input":
                dependencies.add(input_id)
            elif parsed.scheme == "artifact" and parsed.owner != node.id:
                dependencies.add(parsed.owner)
        steps.append(
            {
                "id": node.id,
                "handler": "studio.transform",
                "needs": sorted(dependencies),
                "parameters": node.parameters,
                "cacheable": node.cache_policy.mode == "content_addressed",
                "side_effects": node.side_effects,
                "retry_policy": "none",
                "output_artifacts": [p.path for p in node.outputs.values()],
            }
        )
        operations[node.id] = {
            **node.model_dump(),
            "implementation": tools[f"{node.tool}@{node.tool_version}"].implementation,
        }
        if node.kind == "composite_branch":
            from .branch_lowering import lower_branch

            operations[node.id]["branch"] = lower_branch(node, tools)
            steps[-1]["output_artifacts"].append("branch.json")
    return {
        "ir_hash": ir_fingerprint(ir),
        "runtime_workflow": {
            "schema_version": 1,
            "runtime": "0.2",
            "id": ir.workflow_id,
            "steps": steps,
            "outputs": {name: parse_reference(ref).owner for name, ref in ir.outputs.items()},
        },
        "operations": operations,
        "input_step": input_id,
        "input_name": next(iter(ir.inputs)),
        "ir": ir.model_dump(),
    }
