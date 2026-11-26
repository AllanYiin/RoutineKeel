"""Search data-flow subgraphs over observed tools without calling them."""

from collections import deque
from dataclasses import dataclass
from itertools import islice, product
from time import monotonic

from pydantic import Field, ValidationError
from routinekeel_contracts.bindings import ImplementationAdapter
from routinekeel_contracts.domain_assets import CapabilityPort, content_hash
from routinekeel_contracts.graph import GraphPort, Task
from routinekeel_contracts.graph_validator import compatible, validate_blueprint
from routinekeel_contracts.models import Contract, Identifier

from ..blueprints.source import require
from ..runtime import digest
from .portable import _catalog_adapters, installed_local_tools


@dataclass(frozen=True)
class ObservedTool:
    adapter: ImplementationAdapter
    binding: dict
    proof: str
    destination: str | None


@dataclass(frozen=True)
class AvailablePort:
    reference: str
    port: CapabilityPort
    duration_ms: int | None = 0


@dataclass(frozen=True)
class SearchState:
    steps: tuple = ()
    values: tuple = ()
    cost: int = 0


class SuggestedStep(Contract):
    id: Identifier
    tool: str = Field(min_length=1, max_length=200)
    inputs: dict[Identifier, str]


class SuggestedChain(Contract):
    steps: list[SuggestedStep] = Field(min_length=2, max_length=8)
    outputs: dict[Identifier, str] = Field(min_length=1)


def _edge_matches(actual, expected):
    return bool(
        expected.schema_id
        and actual.schema_id == expected.schema_id
        and compatible(actual, expected)
    )


def observed_tools(workspace, inventory, *, include_pending=False):
    """Use the usual tool admission checks on each tool's actual transport ports."""
    for name, record in sorted((installed_local_tools() | inventory).items()):
        if not isinstance(record, dict) or "portable" not in record:
            continue
        try:
            adapter = ImplementationAdapter.model_validate(record["portable"])
            node = Task(
                kind="task",
                id="probe",
                capability=adapter.capability,
                capability_version=adapter.capability_version,
                permissions=adapter.permissions,
                side_effect=adapter.side_effect,
                inputs={
                    key: {"source": f"input://{key}", "port": GraphPort.model_validate(port)}
                    for key, port in (record.get("inputs") or {}).items()
                },
                outputs={
                    key: GraphPort.model_validate(port)
                    for key, port in (record.get("outputs") or {}).items()
                },
            )
        except (ValidationError, AttributeError):
            continue
        accepted, _ = _catalog_adapters(
            node, workspace, {name: record}, include_pending=include_pending
        )
        for declared, binding, proof, allowed, destination, _identity in accepted:
            if allowed and binding is not None:
                yield ObservedTool(declared, binding, proof, destination)


def _eligible(operation, task, rule, offline):
    tool = operation.adapter
    resources = operation.binding.get("resources") or {}
    model_calls = resources.get("model_calls", 0)
    return (
        set(tool.permissions) <= set(task.permissions)
        and type(model_calls) is int
        and model_calls >= 0
        and "model_call_batch" not in resources
        and (not rule.execution_loci or tool.locus in rule.execution_loci)
        and (tool.side_effect == "none" or tool.side_effect == task.side_effect)
        and (not (offline or rule.constraints.offline_only) or not tool.network)
        and (not tool.network or operation.destination in rule.allowed_destinations)
        and (
            not rule.allowed_destinations
            or not operation.destination
            or operation.destination in rule.allowed_destinations
        )
        and (
            rule.constraints.max_data_age_seconds is None
            or (
                tool.max_data_age_seconds is not None
                and tool.max_data_age_seconds <= rule.constraints.max_data_age_seconds
            )
        )
    )


def _available_ports(ports, semantics):
    return {
        name: CapabilityPort(**ports[name].model_dump(), schema_id=semantics[name])
        for name in sorted(ports)
    }


def _references(available, ports):
    return [
        [item for item in available if _edge_matches(item.port, ports[key])]
        for key in sorted(ports)
    ]


def _graph_port(port):
    return GraphPort(
        type=port.type,
        format=port.format,
        required=port.required,
        items=_graph_port(port.items) if port.items is not None else None,
    ).model_dump(mode="json", exclude_none=True)


def compile_toolchain(task, rule, plan, operations, workflow, workspace, *, offline=False):
    """Compile a proposed chain into ordinary Graph tasks and concrete bindings."""
    from .builder import compile_plan_tree

    require(not task.parameters, "工具鏈需要可驗證的參數映射。")
    require(
        plan["plan_id"]
        == content_hash(
            {
                key: value
                for key, value in plan.items()
                if key not in {"plan_id", "qualification", "compiled_graph_hash"}
            }
        ),
        "工具鏈內容已變更。",
    )
    require(
        plan["requirement_id"] == rule.id and plan["capability"] == rule.capability,
        "工具鏈與節點能力需求不符。",
    )
    live = {
        (entry.adapter.ref, entry.proof, entry.binding["fingerprint"]): entry
        for entry in operations
    }
    graph_inputs = {name: item.port for name, item in task.inputs.items()}
    values = {
        f"input://{key}": port
        for key, port in _available_ports(graph_inputs, rule.input_semantics).items()
    }
    outputs = _available_ports(task.outputs, rule.output_semantics)
    graph_nodes, bindings = [], {}
    cost, permissions = 0, set()
    duration = {reference: 0 for reference in values}
    side_effects = set()
    for step in plan["steps"]:
        tool = live.get((step["adapter"], step["implementation_hash"], step["binding_fingerprint"]))
        require(
            tool is not None and _eligible(tool, task, rule, offline),
            "工具鏈的 Adapter 或授權已變更。",
        )
        descriptor = tool.adapter
        require(not tool.binding.get("preparation_pending"), "工具鏈的配方尚未完成準備。")
        require(
            descriptor.inputs.keys() == step["inputs"].keys()
            and all(
                source in values and _edge_matches(values[source], descriptor.inputs[name])
                for name, source in step["inputs"].items()
            ),
            "工具鏈資料邊已變更或無相符的資料語意。",
        )
        cost += descriptor.max_cost_micro_usd
        permissions.update(descriptor.permissions)
        side_effects.add(descriptor.side_effect)
        input_durations = [duration[source] for source in step["inputs"].values()]
        milliseconds = (
            max(input_durations, default=0) + descriptor.max_latency_ms
            if descriptor.max_latency_ms is not None
            and all(value is not None for value in input_durations)
            else None
        )
        maximum_latency = rule.constraints.max_latency_ms
        require(
            maximum_latency is None or milliseconds is not None and milliseconds <= maximum_latency,
            "工具鏈的延遲超過原需求上限。",
        )
        graph_nodes.append(
            {
                "id": step["id"],
                "kind": "task",
                "capability": tool.binding["tool"],
                "capability_version": tool.binding["version"],
                "permissions": descriptor.permissions,
                "side_effect": descriptor.side_effect,
                "inputs": {
                    name: {"source": source, "port": _graph_port(descriptor.inputs[name])}
                    for name, source in step["inputs"].items()
                },
                "outputs": {name: _graph_port(port) for name, port in descriptor.outputs.items()},
            }
        )
        bindings[step["id"]] = tool.binding
        for name, port in descriptor.outputs.items():
            reference = f"artifact://{step['id']}/{name}"
            values[reference] = port
            duration[reference] = milliseconds
    require(
        rule.portability == "flexible"
        and len(graph_nodes) >= 2
        and permissions == set(task.permissions)
        and task.side_effect in side_effects
        and cost == plan["cost_micro_usd"]
        and cost <= rule.constraints.max_cost_micro_usd,
        "工具鏈成本、權限或副作用與原需求不符。",
    )
    require(
        plan["outputs"].keys() == outputs.keys()
        and all(
            source in values and _edge_matches(values[source], outputs[name])
            for name, source in plan["outputs"].items()
        ),
        "工具鏈的最終資料埠與原需求不符。",
    )
    graph, _ = validate_blueprint(
        {
            "blueprint_schema_version": 2,
            "workflow": workflow,
            "inputs": {key: _graph_port(port) for key, port in graph_inputs.items()},
            "nodes": graph_nodes,
            "outputs": plan["outputs"],
            "quality_contract": {},
            "provenance": {"toolchain_plan_id": plan["plan_id"]},
        }
    )
    resolved = {
        "bindings": bindings,
        "fingerprint": digest(
            {"graph": graph.model_dump(mode="json"), "bindings": bindings, "gaps": []}
        ),
    }
    compiled = compile_plan_tree(
        graph.model_dump(mode="json", exclude_none=True), plan["plan_id"], workspace, resolved
    )
    require(
        plan.get("compiled_graph_hash", compiled["graph_hash"]) == compiled["graph_hash"],
        "已編譯的工具鏈指紋不同。",
    )
    return compiled, bindings


def _proposed_graph(task, rule, state, outputs):
    selected = set()
    pending = list(outputs.values())
    steps = {row["id"]: row for row in state.steps}
    while pending:
        reference = pending.pop()
        if not reference.startswith("artifact://"):
            continue
        step_id = reference.removeprefix("artifact://").split("/", 1)[0]
        if step_id not in selected:
            selected.add(step_id)
            pending.extend(steps[step_id]["inputs"].values())
    if len(selected) < 2:
        return None
    included = [step for step in state.steps if step["id"] in selected]
    if set().union(*(step["permissions"] for step in included)) != set(task.permissions):
        return None
    if task.side_effect == "unresolved" or (
        task.side_effect != "none"
        and not any(step["side_effect"] == task.side_effect for step in included)
    ):
        return None
    required_capability = {"capability": rule.capability, "requirement_id": rule.id}
    graph = {
        **required_capability,
        "steps": included,
        "outputs": outputs,
        "cost_micro_usd": sum(step["cost_micro_usd"] for step in included),
    }
    return {"plan_id": content_hash(graph), **graph, "qualification": "NOT_RUN"}


def chain_candidate(task, rule, plan, operations, workflow, workspace, *, offline=False):
    compiled, bindings = compile_toolchain(
        task, rule, plan, operations, workflow, workspace, offline=offline
    )
    chain = {**plan, "compiled_graph_hash": compiled["graph_hash"]}
    identity = "routinekeel.chain_" + plan["plan_id"][:48]
    local = all(
        binding["provider"] in {"builtin", "portable_local"} for binding in bindings.values()
    )
    resources = {
        "work": len(bindings),
        "cost_micro_usd": plan["cost_micro_usd"],
        "model_calls": sum(
            (binding.get("resources") or {}).get("model_calls", 0) for binding in bindings.values()
        ),
    }
    transport = {
        "provider": "toolchain",
        "tool": identity,
        "version": "1.0.0",
        "chain_plan": compiled,
        "chain_bindings": bindings,
        "network": any(binding.get("network") for binding in bindings.values()),
        "side_effect": task.side_effect,
        "permissions": sorted(task.permissions),
        "resources": resources,
    }
    transport["fingerprint"] = digest(transport)
    return {
        "adapter": identity + "@1.0.0",
        "adapter_id": identity,
        "tool": identity,
        "provider_version": "1.0.0",
        "model_version": None,
        "locus": "local_runtime" if local else "host_mediated",
        "schema_hash": digest(
            [compiled["graph"]["inputs"], task.model_dump(mode="json")["outputs"]]
        ),
        "environment_fingerprint": transport["fingerprint"],
        "implementation_hash": digest(compiled),
        "permission_scope": sorted(task.permissions),
        "cost_micro_usd": resources["cost_micro_usd"],
        "toolchain_plan": chain,
        "contract_match": True,
        "behavioral_verdict": "NOT_RUN",
        "allowed": True,
    }, transport


def execute_local_chain(binding, inputs, *, workspace=None):
    from .qualification import execute_local

    plan, bindings = binding["chain_plan"], binding["chain_bindings"]
    require(
        plan["bindings_hash"] == digest(bindings) and plan["graph_hash"] == digest(plan["graph"]),
        "工具鏈的 Graph 或綁定指紋不同。",
    )
    values = {f"input://{key}": value for key, value in inputs.items()}
    nodes = {node["id"]: node for node in plan["graph"]["nodes"]}
    for step_id in plan["order"]:
        node, child = nodes[step_id], bindings[step_id]
        require(
            child["provider"] in {"builtin", "portable_local"} and not child["network"],
            "此工具鏈含需要宿主處理的操作。",
        )
        args = {name: values[spec["source"]] for name, spec in node["inputs"].items()}
        outputs = execute_local(child, node, args, node.get("parameters", {}), workspace=workspace)
        values.update({f"artifact://{step_id}/{name}": output for name, output in outputs.items()})
    return {name: values[source] for name, source in plan["final_outputs"].items()}


def _work_items(state, expected, operations, rule, maximum_steps):
    for assigned in product(*_references(state.values, expected)):
        yield None, assigned
    if len(state.steps) < maximum_steps:
        for operation in operations:
            tool = operation.adapter
            if state.cost + tool.max_cost_micro_usd <= rule.constraints.max_cost_micro_usd:
                for assigned in product(*_references(state.values, tool.inputs)):
                    yield operation, assigned


def _extend(state, operation, source, rule, seen):
    tool = operation.adapter
    latency = (
        max((port.duration_ms for port in source), default=0) + tool.max_latency_ms
        if tool.max_latency_ms is not None and all(port.duration_ms is not None for port in source)
        else None
    )
    maximum_latency = rule.constraints.max_latency_ms
    if maximum_latency is not None and (latency is None or latency > maximum_latency):
        return None
    mapping = {key: port.reference for key, port in zip(sorted(tool.inputs), source, strict=True)}
    identifier = "chain_" + content_hash([tool.ref, operation.proof, mapping])[:24]
    current_ids = tuple(step["id"] for step in state.steps)
    signature = tuple(sorted((*current_ids, identifier)))
    if identifier in current_ids or signature in seen:
        return None
    seen.add(signature)
    step = {
        "id": identifier,
        "adapter": tool.ref,
        "tool": tool.tool,
        "version": tool.provider_version,
        "implementation_hash": operation.proof,
        "binding_fingerprint": operation.binding["fingerprint"],
        "inputs": mapping,
        "permissions": sorted(tool.permissions),
        "side_effect": tool.side_effect,
        "cost_micro_usd": tool.max_cost_micro_usd,
    }
    outputs = tuple(
        AvailablePort(f"artifact://{identifier}/{name}", port, latency)
        for name, port in sorted(tool.outputs.items())
    )
    return SearchState(
        (*state.steps, step), (*state.values, *outputs), state.cost + tool.max_cost_micro_usd
    )


def normalize_suggestion(raw, task, rule, operations, *, offline=False):
    """Rebuild an inert host proposal with current observations and stable step IDs."""
    proposal = SuggestedChain.model_validate(raw)
    require(
        rule.portability == "flexible"
        and rule.input_semantics.keys() == task.inputs.keys()
        and rule.output_semantics.keys() == task.outputs.keys(),
        "工具鏈提案需要可替換能力及完整的資料語意。",
    )
    require(len({step.id for step in proposal.steps}) == len(proposal.steps), "提案步驟識別重複。")
    require(proposal.outputs.keys() == task.outputs.keys(), "提案必須保留藍圖的所有交付埠。")
    inputs = _available_ports(
        {key: value.port for key, value in task.inputs.items()}, rule.input_semantics
    )
    expected = _available_ports(task.outputs, rule.output_semantics)
    values = {
        f"input://{key}": AvailablePort(f"input://{key}", port) for key, port in inputs.items()
    }
    state = SearchState(values=tuple(values.values()))
    seen = {()}
    for step in proposal.steps:
        matches = [
            item
            for item in operations
            if item.adapter.tool == step.tool and _eligible(item, task, rule, offline)
        ]
        require(len(matches) == 1, "提案工具未觀測到、有歧義或不符合授權：" + step.id)
        operation = matches[0]
        ports = operation.adapter.inputs
        require(
            step.inputs.keys() == ports.keys()
            and all(
                reference in values and _edge_matches(values[reference].port, ports[key])
                for key, reference in step.inputs.items()
            ),
            "提案資料來源或埠語意不符：" + step.id,
        )
        source = tuple(values[step.inputs[key]] for key in sorted(ports))
        successor = _extend(state, operation, source, rule, seen)
        require(
            successor is not None and successor.cost <= rule.constraints.max_cost_micro_usd,
            "提案超出成本、延遲或重複步驟限制：" + step.id,
        )
        for port in successor.values[len(state.values) :]:
            output = port.reference.rsplit("/", 1)[1]
            values[f"artifact://{step.id}/{output}"] = port
        state = successor
    require(
        all(
            reference in values and _edge_matches(values[reference].port, expected[key])
            for key, reference in proposal.outputs.items()
        ),
        "提案的最終輸出語意不符。",
    )
    outputs = {key: values[reference].reference for key, reference in proposal.outputs.items()}
    plan = _proposed_graph(task, rule, state, outputs)
    require(
        plan is not None and len(plan["steps"]) == len(proposal.steps),
        "提案含多餘步驟或權限作用範圍不符。",
    )
    return plan


def _serialize(state):
    return {
        "steps": list(state.steps),
        "cost": state.cost,
        "values": [
            [entry.reference, entry.port.model_dump(mode="json"), entry.duration_ms]
            for entry in state.values
        ],
    }


def _restore(body):
    return SearchState(
        tuple(body["steps"]),
        tuple(
            AvailablePort(ref, CapabilityPort.model_validate(port), duration)
            for ref, port, duration in body["values"]
        ),
        body["cost"],
    )


def search_toolchains(
    task,
    rule,
    operations,
    *,
    offline=False,
    maximum_states=512,
    maximum_steps=8,
    timeout_seconds=2,
    checkpoint=None,
    capture_checkpoint=False,
):
    """Bounded topological search. An eligible chain remains an untested proposal."""
    if rule.portability != "flexible":
        return {"status": "not_substitutable", "plans": [], "evaluated": 0}
    if (
        rule.input_semantics.keys() != task.inputs.keys()
        or rule.output_semantics.keys() != task.outputs.keys()
    ):
        return {"status": "needs_port_semantics", "plans": [], "evaluated": 0}
    inputs = _available_ports(
        {key: value.port for key, value in task.inputs.items()}, rule.input_semantics
    )
    expected = _available_ports(task.outputs, rule.output_semantics)
    initial = SearchState(
        values=tuple(AvailablePort(f"input://{key}", port) for key, port in inputs.items())
    )
    ordered = sorted(
        (item for item in operations if _eligible(item, task, rule, offline)),
        key=lambda item: (item.adapter.ref, item.proof, item.adapter.tool),
    )
    if checkpoint is None:
        frontier, seen, plans, prefix_count, offset = deque([initial]), {()}, {}, 0, 0
    else:
        frontier = deque(_restore(row) for row in checkpoint["frontier"])
        seen = {tuple(row) for row in checkpoint["seen"]}
        plans = {plan["plan_id"]: plan for plan in checkpoint["plans"]}
        prefix_count, offset = checkpoint["evaluated"], checkpoint["offset"]
    evaluated = 0
    deadline = monotonic() + timeout_seconds
    while frontier and len(plans) < 5:
        state = frontier.popleft()
        items = islice(_work_items(state, expected, ordered, rule, maximum_steps), offset, None)
        for operation, assigned in items:
            if evaluated >= maximum_states or monotonic() > deadline:
                result = {
                    "status": "search_limit_reached",
                    "plans": list(plans.values()),
                    "evaluated": prefix_count + evaluated,
                }
                if capture_checkpoint:
                    result["checkpoint"] = {
                        "frontier": [_serialize(row) for row in (state, *frontier)],
                        "offset": offset,
                        "seen": [list(row) for row in sorted(seen)],
                        "plans": result["plans"],
                        "evaluated": result["evaluated"],
                    }
                return result
            evaluated += 1
            offset += 1
            if operation is None:
                outputs = {
                    key: value.reference
                    for key, value in zip(sorted(expected), assigned, strict=True)
                }
                plan = _proposed_graph(task, rule, state, outputs)
                if plan:
                    plans.setdefault(plan["plan_id"], plan)
                    if len(plans) >= 5:
                        break
            else:
                successor = _extend(state, operation, assigned, rule, seen)
                if successor is not None:
                    frontier.append(successor)
        offset = 0
    return {
        "status": "proposed" if plans else "no_chain",
        "plans": list(plans.values()),
        "evaluated": prefix_count + evaluated,
    }
