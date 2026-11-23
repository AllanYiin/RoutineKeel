"""Check provenance and delivery gates on the canonical Blueprint Graph."""

from pydantic import Field, JsonValue

from .domain_assets import AssetRef, CapabilityRequirement, Fingerprint, content_hash
from .graph import ForEach, Join, Repeat, Task
from .models import Contract, Identifier
from .references import parse_reference


class DeliveryGate(Contract):
    source: str
    equals: JsonValue
    deliveries: list[Identifier] = Field(min_length=1)


class PatternComposition(Contract):
    ref: AssetRef
    hash: Fingerprint
    pack_ref: AssetRef
    pack_hash: Fingerprint
    qualification_hash: Fingerprint
    decision_id: Fingerprint
    namespace: Identifier
    members: dict[Identifier, Fingerprint] = Field(min_length=1)
    requirements: dict[str, CapabilityRequirement]
    gate_kind: Identifier | None = None
    gate: DeliveryGate | None = None
    quality_contract: dict[Identifier, JsonValue] = Field(default_factory=dict)
    quality_outputs: dict[Identifier, str] = Field(default_factory=dict)


def _successful_dependencies(graph, order, dependencies):
    nodes = {node.id: node for node in graph.nodes}
    guaranteed = {}
    for key in order:
        node = nodes[key]
        if isinstance(node, Join) and node.policy == "all_done":
            ancestry = set()
        elif isinstance(node, Join) and node.policy == "selected_branch":
            paths = [
                set().union(
                    *(guaranteed[parse_reference(value).owner] for value in branch.values())
                )
                for branch in node.branches.values()
            ]
            ancestry = set.intersection(*paths) | guaranteed[node.switch]
        else:
            ancestry = set().union(*(guaranteed[parent] for parent in dependencies[key]))
        guaranteed[key] = ancestry | {key}
    return guaranteed


def _task_paths(node, path):
    if isinstance(node, Task):
        yield path, node
    elif isinstance(node, (ForEach, Repeat)):
        for child in node.body.nodes:
            yield from _task_paths(child, path + "/" + child.id)


def validate_pattern_records(graph, order, dependencies):
    from pydantic import ValidationError

    from .graph_validator import port_for, require

    raw = graph.provenance.get("pattern_compositions", [])
    require(isinstance(raw, list), "Pattern 合成資訊須是具版本的清單")
    try:
        records = [PatternComposition.model_validate(record) for record in raw]
    except ValidationError:
        require(False, "Pattern 合成資訊格式不符")
    nodes = {node.id: node for node in graph.nodes}
    covered = set()
    namespaces = set()
    gate_kinds = set()
    guaranteed = _successful_dependencies(graph, order, dependencies) if records else {}
    for record in records:
        require(record.namespace not in namespaces, "Pattern namespace 重複")
        namespaces.add(record.namespace)
        require(
            record.members.keys() <= nodes.keys() and not covered.intersection(record.members),
            "Pattern 合成節點遺失或重複",
        )
        for key, fingerprint in record.members.items():
            require(
                content_hash(nodes[key].model_dump(mode="json", exclude_none=True)) == fingerprint,
                "Pattern 合成節點的內容或連線已變更",
            )
        covered.update(record.members)
        required_tasks = dict(
            item for member in record.members for item in _task_paths(nodes[member], member)
        )
        require(
            required_tasks.keys() == record.requirements.keys(), "Pattern 能力需求與 Graph 不符"
        )
        for key, node in required_tasks.items():
            rule = record.requirements[key]
            require(
                rule.capability == node.capability
                and rule.version in {None, node.capability_version},
                "Pattern 能力版本與工作節點不符",
            )
        for value in record.quality_outputs.values():
            ref = parse_reference(value)
            require(
                ref.scheme == "artifact" and ref.owner in record.members,
                "Pattern 品質評估須採用此子圖的具名輸出",
            )
            port_for(graph, nodes, value)
        review = record.quality_contract.get("review_policy", {})
        require(isinstance(review, dict), "Pattern 人審政策格式不符")
        if review.get("required"):
            node_id = review.get("approved_node")
            require(
                node_id in record.members and nodes[node_id].kind == "human_review",
                "Pattern 必要人審須綁定此子圖中的人審節點",
            )
        require((record.gate is None) == (record.gate_kind is None), "必要品質 Gate 缺少判定條件")
        if record.gate:
            require(record.gate_kind not in gate_kinds, "必要品質 Gate 重複")
            gate_kinds.add(record.gate_kind)
            port = port_for(graph, nodes, record.gate.source)
            gate = parse_reference(record.gate.source)
            require(
                gate.scheme == "artifact" and gate.owner in record.members and port.required,
                "品質 Gate 須使用此 Pattern 的必要產物",
            )
            require(
                len(set(record.gate.deliveries)) == len(record.gate.deliveries)
                and set(record.gate.deliveries) <= graph.outputs.keys(),
                "品質 Gate 鎖定的交付範圍不完整",
            )
            for name in record.gate.deliveries:
                output = parse_reference(graph.outputs[name])
                require(gate.owner in guaranteed[output.owner], "正式交付路徑可繞過必要品質 Gate")
    require({key for key in nodes if key.startswith("rwp_")} <= covered, "Pattern 節點缺少合成證據")
