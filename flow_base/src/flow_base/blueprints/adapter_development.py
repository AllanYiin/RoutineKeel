"""Carry chosen tool proposals into adapter implementation and qualification."""

from ..runtime import FlowError, digest
from .source import require

LOCAL_APPROACHES = {"local_tool", "skill_code", "local_implementation"}


def work_orders(state, tasks, rules):
    orders = []
    gaps = {item["id"]: item for item in state["gaps"]}
    for row in state["binding_plan"]["automatic_work"]:
        node = row["node"]
        if node not in rules:
            continue
        selected = row["selection"]
        option_id = selected["option_id"] if selected else row["recommended_option_id"]
        option = next((item for item in row["options"] if item["id"] == option_id), None)
        if not option or option["approach"] == "observed_adapter":
            continue
        if not selected and sum(item["approach"] == row["handled_by"] for item in row["options"]) != 1:
            continue
        rule, task = rules[node], tasks[node]
        local = option["approach"] in LOCAL_APPROACHES and (
            not rule.execution_loci or "local_runtime" in rule.execution_loci
        )
        if not local and option["approach"] not in {"host_model", "host_tool"}:
            continue
        contract = row["contract"]
        descriptor = {
            "capability": rule.capability,
            "capability_version": rule.version or task.capability_version,
            "locus": "local_runtime" if local else "host_mediated",
            "permissions": task.permissions,
            "side_effect": task.side_effect,
            "max_cost_micro_usd": rule.constraints.max_cost_micro_usd,
        }
        for direction, semantics in (("inputs", rule.input_semantics), ("outputs", rule.output_semantics)):
            descriptor[direction] = {
                key: {**port, **({"schema_id": semantics[key]} if key in semantics else {})}
                for key, port in contract[direction].items()
            }
        scope = digest([state["draft_id"], node, option_id, descriptor, task.parameters])
        gap = gaps[row["gap_id"]]
        candidates = [candidate["adapter"] for candidate in gap.get("candidates", [])
                      if candidate["allowed"] and not candidate["declined"]
                      and candidate["locus"] == descriptor["locus"]]
        orders.append({
            "node": node, "scope_hash": scope, "option": option, "descriptor": descriptor,
            "capability_version_range": rule.version_range,
            "parameters": task.parameters, "skills": row["skills"], "candidates": candidates,
            "status": "qualify" if candidates else "implement",
            "entry_point": "routinekeel.portable_adapters" if local else "host_mcp",
            "handler": ("handler(inputs, parameters, *, assets)" if any(
                port["type"] == "file" for ports in (descriptor["inputs"], descriptor["outputs"])
                for port in ports.values()
            ) else "handler(inputs, parameters)") if local else None,
            "submit_action": "complete_local_adapter" if local else None,
        })
    return orders


def finish_local(service, state, payload):
    """Use the captured tool choice to qualify code independently observed by the Runtime."""
    from ..graph.qualification import validate_adapter_cases

    require(set(payload) <= {"action", "request_id", "expected_revision", "node", "scope_hash", "adapter", "cases"},
            "Adapter 開發回報參數不符。")
    node = payload.get("node")
    order = next((item for item in state.get("adapter_development", []) if item["node"] == node), None)
    if not order or payload.get("scope_hash") != order["scope_hash"]:
        raise FlowError("REVISION_CONFLICT", "Adapter 方案或節點契約已變更，請重讀開發工作。")
    adapter = payload.get("adapter")
    require(order["submit_action"] == "complete_local_adapter" and adapter in order["candidates"],
            "底座尚未驗證此本機 Adapter 的安裝入口與契約。")
    gap = next(item for item in state["gaps"] if item["location"] == node and item["kind"] == "capability")
    candidate = next(item for item in gap["candidates"] if item["adapter"] == adapter)
    cases = state.get("adapter_case_supplies", {})
    if "cases" in payload:
        validate_adapter_cases(service.workspace, state["draft_id"], node, payload["cases"])
        cases = {**cases, node: payload["cases"]}
    return {
        **state,
        "adapter_case_supplies": cases,
        "adapter_choices": {**state.get("adapter_choices", {}), node: {
            "adapter": adapter, "environment_fingerprint": candidate["environment_fingerprint"],
            "scope_hash": gap["scope_hash"],
        }},
        "adapter_development_submissions": {**state.get("adapter_development_submissions", {}), node: {
            "adapter": adapter, "option_id": order["option"]["id"], "scope_hash": order["scope_hash"],
        }},
    }
