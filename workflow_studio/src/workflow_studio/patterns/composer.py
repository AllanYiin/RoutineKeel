"""Expand an approved Pattern inside a new revision of the existing Blueprint draft."""

import re

from pydantic import Field, ValidationError
from routinekeel_contracts.domain_assets import (
    bind_profile_parameters,
    content_hash,
    graph_tasks,
)
from routinekeel_contracts.graph import Graph
from routinekeel_contracts.graph_validator import (
    compatible,
    port_for,
    reference,
    validate_blueprint,
    validate_structure,
)
from routinekeel_contracts.models import Contract, Identifier
from routinekeel_contracts.pattern_composition import DeliveryGate, PatternComposition
from routinekeel_contracts.privacy import contains_private_data

from ..contracts import StudioError
from .extractor import _remap_inputs

_DESTINATION = re.compile(r"^(?:node://([a-z][a-z0-9_-]{0,63})/)?([a-z][a-z0-9_-]{0,63})$")


class InsertLocation(Contract):
    namespace: Identifier
    after: list[Identifier] = Field(default_factory=list)
    before: list[Identifier] = Field(default_factory=list)
    deliveries: list[Identifier] = Field(default_factory=list)


class PortMap(Contract):
    inputs: dict[Identifier, str]
    outputs: dict[Identifier, list[str]]
    control_inputs: dict[Identifier, str] = Field(default_factory=dict)
    control_outputs: dict[Identifier, list[Identifier]] = Field(default_factory=dict)


def _reject(message, code="PATTERN_GRAPH_INVALID"):
    raise StudioError(code, message)


def _node_id(namespace, name):
    return f"rwp_{namespace[:23]}_{name[:19]}_{content_hash([namespace, name])[:12]}"


def _controls(record, target_names, external, internal, port_map, ids):
    inputs = record.boundary.control_inputs
    outputs = record.boundary.control_outputs
    if (
        inputs.keys() != port_map.control_inputs.keys()
        or outputs.keys() != port_map.control_outputs.keys()
    ):
        _reject("Pattern 控制邊界需要完整對照。")
    for key, port in inputs.items():
        value = port_map.control_inputs[key]
        name, _, label = value.partition("/")
        parent = external.get(name)
        if parent is None:
            _reject("Pattern 控制輸入指向不存在的節點。")
        members = [ids[item] for item in port.nodes]
        if port.kind == "order" and not label:
            for member in members:
                internal[member]["depends_on"].append(name)
        elif (
            port.kind == "route"
            and port.route == label
            and parent["kind"] == "switch"
            and label in parent["routes"]
        ):
            parent["routes"][label].extend(members)
        else:
            _reject("Pattern 控制埠與實際分支標籤不符。")
    for key, port in outputs.items():
        targets = port_map.control_outputs[key]
        if not targets or not set(targets) <= target_names:
            _reject("Pattern 控制輸出需要存在的下游節點。")
        members = [ids[item] for item in port.nodes]
        if port.kind == "order":
            for target in targets:
                external[target]["depends_on"].extend(members)
        else:
            for member in members:
                owner = internal[member]
                if owner["kind"] != "switch" or port.route not in owner["routes"]:
                    _reject("Pattern 路由控制輸出須由同標籤的條件節點發出。")
                owner["routes"][port.route].extend(targets)


def _connect_outputs(base, current, existing, graph, record, port_map, remap):
    if not port_map.outputs.keys() <= record.boundary.outputs.keys():
        _reject("輸出對照含未知的 Pattern 交付埠。")
    changed = set()
    destinations = set()
    for name, mapped in port_map.outputs.items():
        origin = remap(graph.outputs[name])
        source = record.boundary.outputs[name]
        for destination in mapped:
            text = destination.removeprefix("output://")
            match = _DESTINATION.fullmatch(text)
            if not match or match.groups() in destinations:
                _reject("Pattern 輸出目的埠重複或格式錯誤。")
            destinations.add(match.groups())
            node, port = match.groups()
            if node:
                if node not in existing or port not in existing[node]["inputs"]:
                    _reject("找不到要重新接線的下游輸入。")
                existing[node]["inputs"][port]["source"] = origin
            else:
                if port in current.outputs:
                    old_nodes = {node.id: node for node in current.nodes}
                    old_port = port_for(current, old_nodes, current.outputs[port])
                    if not compatible(source, old_port):
                        _reject("Pattern 交付埠型別與被替換的輸出不同。")
                base["outputs"][port] = origin
            changed.add(destination)
    return sorted(changed)


def compose(
    blueprint,
    pattern,
    *,
    location,
    mapping,
    profiles,
    parameter_bindings,
    pack,
    pattern_hash,
    qualification_hash,
    decision_id,
):
    try:
        position = InsertLocation.model_validate(location)
        ports = PortMap.model_validate(mapping)
        graph = bind_profile_parameters(pattern, profiles, parameter_bindings)
    except (ValidationError, TypeError, ValueError):
        _reject("Pattern 的位置、埠對照或 Profile 參數不符合契約。")
    if contains_private_data(parameter_bindings):
        _reject("Profile 含不應匯出的私人資料。", "SECRET_IN_PORTABLE_PACKAGE")
    current, _ = validate_blueprint(blueprint)
    base = current.model_dump(mode="json", exclude_none=True)
    existing = {node["id"]: node for node in base["nodes"]}
    existing_models = {node.id: node for node in current.nodes}
    previous = list(base["provenance"].get("pattern_compositions", []))
    if any(row["namespace"] == position.namespace for row in previous):
        _reject("已有相同的 Pattern namespace。", "PATTERN_DEPENDENCY_CONFLICT")
    if any(row["ref"] == pattern.pattern.ref for row in previous):
        _reject("此 Pattern 已加入草稿。", "PATTERN_DEPENDENCY_CONFLICT")
    if not set(position.after + position.before) <= existing.keys():
        _reject("指定的前後置節點不存在。")
    if ports.inputs.keys() != graph.inputs.keys():
        _reject("Pattern 的資料輸入需要完整且明確的來源。")
    for name, source in ports.inputs.items():
        if not compatible(port_for(current, existing_models, source), graph.inputs[name]):
            _reject("Pattern 資料輸入與上游埠型別不相容。")
    ids = {node.id: _node_id(position.namespace, node.id) for node in graph.nodes}
    if set(ids.values()).intersection(existing):
        _reject("Pattern namespace 與既有節點發生碰撞。")

    def remap(text):
        ref = reference(text)
        if ref.scheme == "input":
            return ports.inputs[ref.owner]
        return f"artifact://{ids[ref.owner]}/{ref.port}"

    local_deps = {}
    validate_structure(graph, max_nodes=401, dependencies=local_deps, extended=True)
    ingresses = [ids[key] for key, parents in local_deps.items() if not parents]
    used = {parent for parents in local_deps.values() for parent in parents}
    exits = [ids[key] for key in local_deps if key not in used]
    internal = {}
    for entry in graph.nodes:
        node = entry.model_dump(mode="json", exclude_none=True)
        node["id"] = ids[entry.id]
        node["depends_on"] = [ids[key] for key in node["depends_on"]]
        if node["id"] in ingresses:
            node["depends_on"].extend(position.after)
        if node["kind"] == "switch":
            node["routes"] = {
                label: [ids[key] for key in keys] for label, keys in node["routes"].items()
            }
        if node["kind"] == "join" and node.get("switch"):
            node["switch"] = ids[node["switch"]]
        _remap_inputs(node, remap)
        internal[node["id"]] = node
    for key in position.before:
        existing[key]["depends_on"].extend(exits)
    _controls(pattern, existing.keys(), existing, internal, ports, ids)
    changed = _connect_outputs(base, current, existing, graph, pattern, ports, remap)
    for node in [*existing.values(), *internal.values()]:
        node["depends_on"] = list(dict.fromkeys(node["depends_on"]))
    base["nodes"].extend(internal.values())
    base["graph_contract_version"] = max(base.get("graph_contract_version", "2.0"), "2.2")
    gate_kind = pattern.triggers.design.gate
    gate = None
    if gate_kind:
        if any(row.get("gate_kind") == gate_kind for row in previous):
            _reject("草稿已包含同類品質 Gate。", "PATTERN_DEPENDENCY_CONFLICT")
        policy = pattern.quality_contract.get("delivery_gate")
        if not isinstance(policy, dict) or policy.keys() - {"port", "equals", "scope"}:
            _reject("必要品質 Gate 缺少明確的成功條件。")
        name = policy.get("port")
        if name not in graph.outputs or "equals" not in policy:
            _reject("必要品質 Gate 須指向具名的 Pattern 輸出。")
        deliveries = position.deliveries or list(base["outputs"])
        if (
            set(deliveries) != base["outputs"].keys()
            and policy.get("scope") != "selected_deliveries"
        ):
            _reject("必要品質 Gate 須涵蓋所有正式交付。")
        gate = DeliveryGate(
            source=remap(graph.outputs[name]),
            equals=policy["equals"],
            deliveries=sorted(deliveries),
        )
    member_graph = Graph.model_validate(
        {
            "inputs": base["inputs"],
            "nodes": list(internal.values()),
            "outputs": base["outputs"],
        }
    )
    members = {
        node.id: content_hash(node.model_dump(mode="json", exclude_none=True))
        for node in member_graph.nodes
    }
    requirements = {}

    def collect(body, prefix=""):
        for node in body.nodes:
            key = prefix + (ids[node.id] if not prefix else node.id)
            if node.kind == "task":
                requirements[key] = next(
                    rule
                    for rule in pattern.requirements
                    if rule.capability == node.capability
                    and rule.accepts_version(node.capability_version)
                )
            elif hasattr(node, "body"):
                collect(node.body, key + "/")

    collect(graph)
    quality = {
        key: value
        for key, value in pattern.quality_contract.items()
        if key in {"evaluators", "invariants", "required_checks", "review_policy"}
    }
    if quality.get("review_policy", {}).get("required"):
        review = quality["review_policy"]
        if review.get("approved_node") not in ids:
            _reject("Pattern 的必要人審位置不完整。")
        quality["review_policy"] = {**review, "approved_node": ids[review["approved_node"]]}
    proof = PatternComposition(
        ref=pattern.pattern.ref,
        hash=pattern_hash,
        pack_ref=pack["ref"],
        pack_hash=pack["manifest_hash"],
        qualification_hash=qualification_hash,
        decision_id=decision_id,
        namespace=position.namespace,
        members=members,
        requirements=requirements,
        gate_kind=gate_kind,
        gate=gate,
        quality_contract=quality,
        quality_outputs={name: remap(value) for name, value in graph.outputs.items()}
        if quality
        else {},
    )
    base["provenance"]["pattern_compositions"] = [
        *previous,
        proof.model_dump(mode="json", exclude_none=True),
    ]
    try:
        composed, order = validate_blueprint(base)
    except StudioError as error:
        code = (
            "PATTERN_CYCLE_DETECTED"
            if "cycle" in str(error).casefold() or "循環" in str(error)
            else error.code
        )
        _reject(str(error), code)
    tasks = list(graph_tasks(composed))
    return {
        "graph": composed.model_dump(mode="json", exclude_none=True),
        "diff": {
            "pattern_ref": pattern.pattern.ref,
            "pattern_hash": pattern_hash,
            "namespace": position.namespace,
            "added_nodes": sorted(internal),
            "changed_outputs": changed,
            "order": order,
            "permissions": sorted(
                {permission for task in tasks for permission in task.permissions}
            ),
            "side_effects": sorted({task.side_effect for task in tasks}),
            "new_requirements": sorted({rule.capability for rule in requirements.values()}),
            "approval_status": "draft_updated",
        },
    }


def compose_project(
    studio,
    project_id,
    *,
    draft_id,
    expected_revision,
    request_id,
    pattern_ref,
    insert_location,
    port_map,
    parameter_bindings,
    decision_id,
    golden_cases=None,
    improvement_id=None,
    target_trial=None,
):
    from ..conversation import blueprints
    from ..conversation.facade import reply_state

    with studio.repository.transaction():
        state = reply_state(studio, project_id, expected_revision)
        registry = studio.tools.packs
        item = registry.pattern_status(pattern_ref)
        decision_ref = pattern_ref
        if improvement_id is not None:
            from .evolution import rehearsal_trial

            improvement, pack = rehearsal_trial(
                studio,
                improvement_id,
                pattern_ref,
                item["content_hash"],
                target_trial,
            )
            decision_ref = improvement["source_ref"]
            qualification_hash = target_trial
        else:
            qualification_hash = item["qualification_hash"]
            if item["status"] != "active" or not registry._has_active_pack(pattern_ref):
                _reject("Pattern 或已試做的 Pack 已停用。", "PATTERN_APPROVAL_REQUIRED")
            pack = registry.get(registry.trial(pattern_ref, qualification_hash)["pack_ref"])
        decision = state.get("pattern_decisions", {}).get(decision_ref, {})
        if (
            decision.get("decision") != "accepted"
            or decision.get("needs_review")
            or decision.get("recommendation_id") != decision_id
        ):
            _reject("此 Pattern 尚未在目前訪談選用，請重新檢視。", "PATTERN_APPROVAL_REQUIRED")
        proposal = state.get("blueprint_proposal", {})
        if proposal.get("proposal_id") != draft_id:
            _reject("找不到指定的 Blueprint 草稿版本。", "REVISION_CONFLICT")
        if registry.get_asset("pattern", decision_ref).sha256 != decision["pattern_hash"]:
            _reject("Pattern 版本已變動或停用。", "PATTERN_APPROVAL_REQUIRED")
        manifest = registry.get_asset("pattern", pattern_ref).model
        existing = proposal["graph"]["provenance"].get("pattern_compositions", [])
        for record in existing:
            old = registry.inspect_pattern(record["ref"], "L2")["design"]
            if (
                record["ref"].split("@")[0] in manifest.triggers.design.conflicts_with
                or manifest.pattern.id in old["conflicts_with"]
            ):
                _reject("此 Pattern 與草稿已選用的 Pattern 互斥。", "PATTERN_DEPENDENCY_CONFLICT")
        schemas = {
            ref: registry.get_asset("profile_schema", ref).model
            for ref in {binding.profile_schema for binding in manifest.profile_parameters}
        }
        result = compose(
            proposal["graph"],
            manifest,
            location=insert_location,
            mapping=port_map,
            profiles=schemas,
            parameter_bindings=parameter_bindings,
            pack=pack,
            pattern_hash=item["content_hash"],
            qualification_hash=qualification_hash,
            decision_id=decision_id,
        )
        graph = result["graph"]
        compositions = graph.pop("provenance")["pattern_compositions"]
        earlier_rehearsal = proposal["graph"]["provenance"].get("pattern_rehearsal")
        if earlier_rehearsal and improvement_id and earlier_rehearsal != improvement_id:
            _reject("同一回歸草稿只能對應一份改善提案。", "REVISION_CONFLICT")
        graph["provenance"] = {}
        if golden_cases is not None:
            graph["quality_contract"]["golden_cases"] = golden_cases
        design = {
            key: value
            for key, value in proposal["decisions"].items()
            if key in {"actors", "preferences", "recovery", "budget", "rules"}
        }
        preview = blueprints.prepare(
            studio,
            project_id,
            expected_revision,
            request_id,
            graph,
            design=design,
            skills=proposal["selections"],
            lineage=proposal.get("lineage"),
            _pattern_compositions=compositions,
            _pattern_rehearsal=earlier_rehearsal or improvement_id,
        )
        return {
            "revision": preview["revision"],
            "from_draft_id": draft_id,
            "proposal_id": preview["proposal_id"],
            "diff": result["diff"],
            "proposal": preview,
        }
