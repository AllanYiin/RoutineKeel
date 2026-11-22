"""Emit Studio WorkflowIR 1.0 and tool signatures for the host-assisted lowering."""

from ..runtime import digest

POLICY_VERSION = "blueprint-ir-record/1"


def check_with_studio(ir, registry, package):
    """Report mandatory checks for both trial and promotion without importing the Studio UI."""
    try:
        from routinekeel_contracts.errors import StudioError
        from routinekeel_contracts.models import ToolSignature
        from routinekeel_contracts.package import verify_package
        from routinekeel_contracts.validator import validate_ir
    except ImportError:
        return {"policy_version": POLICY_VERSION, "status": "NOT_RUN", "reason": "缺少必要的契約驗證器"}
    try:
        validate_ir(ir, {k: ToolSignature.model_validate(v) for k, v in registry["tools"].items()})
        verify_package(package)
    except StudioError as exc:
        return {"policy_version": POLICY_VERSION, "status": "FAIL", "reason": exc.code}
    except Exception as exc:
        return {"policy_version": POLICY_VERSION, "status": "FAIL", "reason": type(exc).__name__}
    return {"policy_version": POLICY_VERSION, "status": "PASS"}


def studio_ir(source, plan):
    schemas = plan["contracts"]
    ports = {}

    def port(name):
        key = "p_" + digest(name)[:16]
        ports[key] = name
        schema = schemas.get(name, {}).get("schema", {})
        kind = schema.get("type", "string")
        return key, {
            "type": kind,
            "required": True,
            "schema_id": "s_" + digest(schema)[:16],
        }

    inputs = dict(port(n) for n in plan["inputs"])
    refs = {n: "input://" + port(n)[0] for n in plan["inputs"]}
    signatures, nodes, evidence = {}, [], []
    platforms = ["windows", "macos", "linux"]
    for i, step in enumerate(plan["steps"]):
        node_id = f"step_{i + 1}"
        tool = "blueprint." + node_id
        kind = {"host": "model_inference", "human": "human_approval"}.get(
            step["mode"], "deterministic"
        )
        side = "external" if step["mode"] == "external" else "local_write"
        in_ports = dict(port(n) for n in step["inputs"])
        out_ports = dict(port(n) for n in step["outputs"])
        bindings = {
            port(n)[0]: {"source": refs[n], "port": port(n)[1]} for n in step["inputs"]
        }
        for name in step["outputs"]:
            refs[name] = f"artifact://{node_id}/" + port(name)[0]
        signature = {
            "tool_id": tool,
            "version": "1.0.0",
            "inputs": in_ports,
            "outputs": out_ports,
            "kind": kind,
            "side_effects": side,
            "platforms": platforms,
        }
        signatures[tool + "@1.0.0"] = signature
        evidence.append(
            {
                "id": node_id,
                "source_id": plan["source_hash"],
                "source_version": 1,
                "evidence_locator": f"workflow.definition.json#/steps/{i}",
                "claim": step["title"],
                "confidence_label": "observed",
            }
        )
        nodes.append(
            {
                "id": node_id,
                "kind": kind,
                "tool": tool,
                "tool_version": "1.0.0",
                "depends_on": [nodes[-1]["id"]] if nodes else [],
                "inputs": bindings,
                "outputs": out_ports,
                "parameters": {
                    "source_step": step["id"],
                    "recipe_hash": digest(step),
                    "review": step["review"],
                },
                "preconditions": [],
                "postconditions": [
                    {"operator": "exists", "reference": refs[n]}
                    for n in step["outputs"]
                ],
                "side_effects": side,
                "retry_policy": {"mode": "never", "max_attempts": 1},
                "cache_policy": {"mode": "none", "fingerprint_fields": []},
                "platform_requirements": platforms,
                "resources": {"network": False, "disk_mb": 0, "memory_mb": 0},
                "timeout_seconds": 120,
                "evidence_refs": [node_id],
            }
        )
    ir = {
        "ir_version": "1.0",
        "workflow_id": plan["workflow"],
        "revision": 1,
        "goal": {
            "description": source["manifest"]["title"],
            "success_criteria": plan["final_outputs"],
        },
        "inputs": inputs,
        "secrets": [],
        "nodes": nodes,
        "outputs": {port(n)[0]: refs[n] for n in plan["final_outputs"]},
        "evidence": evidence,
    }
    return ir, {"ports": ports, "tools": signatures}
