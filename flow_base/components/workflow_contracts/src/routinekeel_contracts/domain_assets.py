"""Inert Pack assets shared by design-time and install-time verification."""

import hashlib
import json
from typing import Annotated, Literal

from packaging.specifiers import SpecifierSet
from pydantic import Field, JsonValue, field_validator, model_validator

from .graph import Graph, GraphPort, Task
from .graph_validator import compatible, port_for, validate_structure
from .models import Contract, Identifier, Platform
from .paths import safe_relative

_NAME = r"[a-z][a-z0-9_-]{0,63}(?:\.[a-z][a-z0-9_-]{0,63}){1,7}"
_VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
AssetID = Annotated[str, Field(pattern=f"^{_NAME}$")]
AssetVersion = Annotated[str, Field(pattern=f"^{_VERSION}$", max_length=40)]
AssetRef = Annotated[str, Field(pattern=f"^{_NAME}@{_VERSION}$", max_length=560)]
Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Label = Annotated[str, Field(min_length=1, max_length=4000)]
Locus = Literal["local_runtime", "host_mediated", "external_service"]
SideEffect = Literal["none", "local_write", "external", "unresolved"]


def unique(values):
    if len(set(values)) != len(values):
        raise ValueError("資產宣告含重複識別碼")


def canonical_json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def content_hash(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


class DomainAsset(Contract):
    id: AssetID
    version: AssetVersion

    @property
    def ref(self):
        return f"{self.id}@{self.version}"


class PackIdentity(DomainAsset):
    title: Label
    domains: list[Identifier] = Field(min_length=1)
    publisher: Label
    runtime_compatibility: Label
    trust: Literal["pending_approval"] = "pending_approval"

    @field_validator("runtime_compatibility")
    @classmethod
    def version_specifier(cls, value):
        try:
            SpecifierSet(value)
        except ValueError as exc:
            raise ValueError("Runtime 版本範圍不合法") from exc
        return value

    @field_validator("domains")
    @classmethod
    def distinct_domains(cls, values):
        unique(values)
        return values


class AssetOrigin(Contract):
    source: Label
    license: Label
    source_sha256: Fingerprint | None = None


class PackManifest(Contract):
    pack_schema_version: Literal["1.0"]
    pack: PackIdentity
    capability_refs: list[AssetRef] = Field(default_factory=list)
    pattern_refs: list[AssetRef] = Field(default_factory=list)
    policy_refs: list[AssetRef] = Field(default_factory=list)
    profile_schema_refs: list[AssetRef] = Field(default_factory=list)
    dependencies: list[AssetRef] = Field(default_factory=list)
    provenance: AssetOrigin
    assets: dict[str, Fingerprint] = Field(min_length=1)

    @model_validator(mode="after")
    def inventory(self):
        unique(
            [
                *self.capability_refs,
                *self.pattern_refs,
                *self.policy_refs,
                *self.profile_schema_refs,
            ]
        )
        unique(self.dependencies)
        unique([path.casefold() for path in self.assets])
        for path in self.assets:
            safe_relative(path)
        if any(ref.split("@")[0] == self.pack.id for ref in self.dependencies):
            raise ValueError("Pack 不可依賴自身的任一版本")
        return self


class CapabilityPort(GraphPort):
    schema_id: AssetID | None = None
    enum: list[str] = Field(default_factory=list)
    items: "CapabilityPort | None" = None

    @model_validator(mode="after")
    def enum_type(self):
        unique(self.enum)
        if self.enum and self.type != "string":
            raise ValueError("列舉值需使用文字埠")
        return self


class Substitution(Contract):
    mode: Literal["flexible", "pinned"]
    requires: list[Literal["contract_conformance", "behavioral_qualification"]]

    @model_validator(mode="after")
    def required_checks(self):
        unique(self.requires)
        if set(self.requires) != {"contract_conformance", "behavioral_qualification"}:
            raise ValueError("能力綁定需同時驗證介面和行為")
        return self


class CapabilityContract(DomainAsset):
    kind: Literal[
        "deterministic", "deterministic_validation", "model_inference", "llm", "external_tool"
    ]
    inputs: dict[Identifier, CapabilityPort]
    outputs: dict[Identifier, CapabilityPort] = Field(min_length=1)
    invariants: list[Label] = Field(min_length=1)
    permissions: list[AssetID]
    side_effects: Literal["none", "local_write", "external"]
    substitution: Substitution
    execution_loci: list[Locus] = Field(min_length=1)
    errors: list[Label] = Field(default_factory=list)
    platforms: list[Platform] = Field(default_factory=list)
    resources: dict[Identifier, int] = Field(default_factory=dict)
    quality: dict[Identifier, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def declarations(self):
        for values in (
            self.invariants,
            self.permissions,
            self.execution_loci,
            self.errors,
            self.platforms,
        ):
            unique(values)
        if any(value < 0 for value in self.resources.values()):
            raise ValueError("能力的資源限制不可為負")
        return self


class PatternSource(Contract):
    workflow_id: Identifier
    version: AssetVersion
    graph_sha256: Fingerprint
    revision: int | None = Field(default=None, ge=1)


class PatternIdentity(DomainAsset):
    title: Label
    kind: Literal["reusable_graph"]
    source: PatternSource | None = None
    declared_domains: list[Identifier] = Field(min_length=1)
    trust_status: Literal["candidate"] = "candidate"


class ControlPort(Contract):
    kind: Literal["order", "route"]
    nodes: list[Identifier] = Field(min_length=1)
    route: Literal["true", "false", "default", "error"] | None = None

    @model_validator(mode="after")
    def route_label(self):
        unique(self.nodes)
        if (self.kind == "route") != (self.route is not None):
            raise ValueError("選路控制埠須帶原分支標籤")
        return self


class PatternBoundary(Contract):
    inputs: dict[Identifier, CapabilityPort]
    outputs: dict[Identifier, CapabilityPort] = Field(min_length=1)
    # Control boundary endpoints retain their source edge semantics for composition.
    control_inputs: dict[Identifier, ControlPort] = Field(default_factory=dict)
    control_outputs: dict[Identifier, ControlPort] = Field(default_factory=dict)


class CapabilityConstraints(Contract):
    contract_hash: Fingerprint | None = None
    max_cost_micro_usd: int = Field(default=0, ge=0)
    max_latency_ms: int | None = Field(default=None, ge=1)
    max_data_age_seconds: int | None = Field(default=None, ge=0)
    numeric_representation: Literal["decimal", "integer", "binary_float"] | None = None
    offline_only: bool = False
    error_codes: list[Identifier] = Field(default_factory=list)


class CapabilityRequirement(Contract):
    id: Identifier
    capability: AssetID
    version: AssetVersion | None = None
    required: bool
    portability: Literal["flexible", "pinned", "unresolved"]
    requires_qualification: Literal[True] = True
    execution_loci: list[Locus] = Field(default_factory=list)
    pinned_adapter: AssetRef | None = None
    fallback_contract: AssetID | None = None
    constraints: CapabilityConstraints = Field(default_factory=CapabilityConstraints)

    @model_validator(mode="after")
    def binding_policy(self):
        unique(self.execution_loci)
        if (self.portability == "pinned") != (self.pinned_adapter is not None):
            raise ValueError("固定依賴須有具版本的 Adapter")
        if self.required and self.fallback_contract is not None:
            raise ValueError("必要能力不得宣告省略交付")
        return self


class DesignTrigger(Contract):
    activation: Literal["suggest"]
    task_categories: list[Identifier]
    intents: list[Identifier]
    positive_examples: list[Label]
    negative_examples: list[Label]
    prerequisites: list[Identifier]
    priority: int = Field(default=50, ge=0, le=100)
    excluded_intents: list[Identifier] = Field(default_factory=list)
    conflicts_with: list[AssetID] = Field(default_factory=list)
    suggests: list[AssetRef] = Field(default_factory=list)
    gate: Identifier | None = None
    privacy: Literal["local_only", "host_mediated", "external"] = "local_only"
    cost_micro_usd: int = Field(default=0, ge=0)


class RuntimeTrigger(Contract):
    activation: Literal["compiled_graph_only"]


class EvolutionTrigger(Contract):
    signals: list[Identifier]


class Triggers(Contract):
    design: DesignTrigger
    runtime: RuntimeTrigger
    evolution: EvolutionTrigger


class PatternTests(Contract):
    golden_cases_manifest: str
    required_edges: list[Identifier] = Field(min_length=1)

    @field_validator("golden_cases_manifest")
    @classmethod
    def safe_path(cls, value):
        return safe_relative(value)


class ProfileParameter(Contract):
    profile_schema: AssetRef
    field: Identifier
    node: Identifier
    parameter: Identifier


def graph_tasks(graph):
    for node in graph.nodes:
        if isinstance(node, Task):
            yield node
        elif hasattr(node, "body"):
            yield from graph_tasks(node.body)


class PatternManifest(Contract):
    pattern_schema_version: Literal["1.0"]
    pattern: PatternIdentity
    boundary: PatternBoundary
    requirements: list[CapabilityRequirement]
    triggers: Triggers
    graph: Graph
    tests: PatternTests
    quality_contract: dict[Identifier, JsonValue] = Field(default_factory=dict)
    policy_refs: list[AssetRef] = Field(default_factory=list)
    profile_schema_refs: list[AssetRef] = Field(default_factory=list)
    profile_parameters: list[ProfileParameter] = Field(default_factory=list)

    @model_validator(mode="after")
    def graph_contract(self):
        unique(self.pattern.declared_domains)
        unique([requirement.id for requirement in self.requirements])
        unique(self.policy_refs)
        unique(self.profile_schema_refs)
        unique(self.tests.required_edges)
        validate_structure(self.graph, max_nodes=401, extended=True)
        if self.boundary.inputs.keys() != self.graph.inputs.keys():
            raise ValueError("Pattern 輸入邊界與 Graph 不一致")
        for name, boundary_port in self.boundary.inputs.items():
            graph_port = self.graph.inputs[name]
            if not (
                compatible(boundary_port, graph_port) and compatible(graph_port, boundary_port)
            ):
                raise ValueError("Pattern 輸入邊界埠型別不一致")
        if self.boundary.outputs.keys() != self.graph.outputs.keys():
            raise ValueError("Pattern 輸出邊界與 Graph 不一致")
        nodes = {node.id: node for node in self.graph.nodes}
        unique([(binding.node, binding.parameter) for binding in self.profile_parameters])
        for binding in self.profile_parameters:
            node = nodes.get(binding.node)
            if (
                binding.profile_schema not in self.profile_schema_refs
                or not isinstance(node, Task)
                or binding.parameter in node.parameters
            ):
                raise ValueError("Profile 參數須對應已宣告的 Schema 和未固定值的工作節點")
        for name, port in self.boundary.outputs.items():
            if not compatible(port_for(self.graph, nodes, self.graph.outputs[name]), port):
                raise ValueError("Pattern 輸出邊界埠型別不相容")
        for edges in (self.boundary.control_inputs, self.boundary.control_outputs):
            for edge in edges.values():
                if not set(edge.nodes) <= nodes.keys():
                    raise ValueError("Pattern 控制邊界缺少有效的節點")
        for node in graph_tasks(self.graph):
            matches = [
                rule
                for rule in self.requirements
                if rule.capability == node.capability
                and rule.version in {None, node.capability_version}
            ]
            if len(matches) != 1:
                raise ValueError("Graph 工作節點須對應唯一的能力需求")
        return self


class Policy(DomainAsset):
    policy_schema_version: Literal["1.0"]
    title: Label
    applicability: Literal["required", "recommended", "optional", "conditional", "prohibited"]
    subjects: list[AssetID] = Field(min_length=1)
    conditions: list[Identifier] = Field(default_factory=list)
    invariants: list[Label] = Field(default_factory=list)

    @model_validator(mode="after")
    def policy_conditions(self):
        for values in (self.subjects, self.conditions, self.invariants):
            unique(values)
        if self.applicability == "conditional" and not self.conditions:
            raise ValueError("條件式 Policy 須宣告適用條件")
        return self


class ProfileField(Contract):
    type: Literal["string", "integer", "number", "boolean", "array", "object"]
    description: Label
    required: bool = False
    enum: list[str] = Field(default_factory=list)
    affects_output: bool

    @model_validator(mode="after")
    def enum_type(self):
        unique(self.enum)
        if self.enum and self.type != "string":
            raise ValueError("Profile 列舉須使用文字")
        return self


class ProfileSchema(DomainAsset):
    profile_schema_version: Literal["1.0"]
    title: Label
    scope: Literal["user", "project", "workflow"]
    fields: dict[Identifier, ProfileField] = Field(min_length=1)


PROFILE_TYPES = {
    "string": {str},
    "integer": {int},
    "number": {int, float},
    "boolean": {bool},
    "array": {list},
    "object": {dict},
}


def profile_value_matches(field, value):
    return type(value) in PROFILE_TYPES[field.type] and (not field.enum or value in field.enum)


def bind_profile_parameters(pattern, schemas, values):
    expected = {(binding.profile_schema, binding.field) for binding in pattern.profile_parameters}
    supplied = {(ref, field) for ref, fields in values.items() for field in fields}
    if expected != supplied:
        raise ValueError("Profile 參數值與宣告的欄位不完整或有多餘欄位")
    graph = pattern.graph.model_copy(deep=True)
    nodes = {node.id: node for node in graph.nodes}
    for binding in pattern.profile_parameters:
        schema = schemas.get(binding.profile_schema)
        scope = (
            schema.fields.get(binding.field)
            if schema and schema.ref == binding.profile_schema
            else None
        )
        value = values[binding.profile_schema][binding.field]
        if scope is None or not profile_value_matches(scope, value):
            raise ValueError("Profile 參數型別或適用範圍不符")
        nodes[binding.node].parameters[binding.parameter] = value
    return graph


ASSET_MODELS = {
    "pack": PackManifest,
    "capability": CapabilityContract,
    "pattern": PatternManifest,
    "policy": Policy,
    "profile_schema": ProfileSchema,
}

CapabilityPort.model_rebuild()


def asset_schemas():
    return {kind: model.model_json_schema() for kind, model in ASSET_MODELS.items()}
