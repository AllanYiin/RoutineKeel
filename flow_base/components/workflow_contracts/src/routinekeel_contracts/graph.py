"""Declarative Blueprint v2. Parsing never loads the named capabilities."""

from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from .models import Contract, Identifier


class GraphPort(Contract):
    type: Literal[
        "string", "integer", "number", "boolean", "object", "array", "file", "json", "null"
    ]
    format: str | None = None
    required: bool = True
    items: "GraphPort | None" = None

    @model_validator(mode="after")
    def collection_element(self):
        if self.items is not None and self.type != "array":
            raise ValueError("只有陣列埠能宣告元素型別")
        return self


class InputBinding(Contract):
    source: str
    port: GraphPort


class Predicate(Contract):
    source: str
    op: Literal["equals", "less_than", "greater_than"]
    value: str | int | float | bool


class Limits(Contract):
    timeout_seconds: int = Field(gt=0, le=86400)
    max_work: int = Field(gt=0, le=10000)
    max_model_calls: int = Field(ge=0)
    max_cost_micro_usd: int = Field(ge=0)


class Node(Contract):
    id: Identifier
    depends_on: list[Identifier] = Field(default_factory=list)
    inputs: dict[Identifier, InputBinding] = Field(default_factory=dict)
    outputs: dict[Identifier, GraphPort] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)


class Task(Node):
    kind: Literal["task"]
    capability: str = Field(min_length=1)
    capability_version: str = Field(min_length=1)
    permissions: list[str]
    side_effect: Literal["none", "local_write", "external", "unresolved"]
    parameters: dict[str, JsonValue] = Field(default_factory=dict)


class Review(Node):
    kind: Literal["human_review"]
    prompt: str = Field(min_length=1)


class Switch(Node):
    kind: Literal["switch"]
    condition: Predicate
    routes: dict[Literal["true", "false"], list[Identifier]]
    on_error: Literal["failed", "review_required"]


class Join(Node):
    kind: Literal["join"]
    policy: Literal["all_success", "all_done", "selected_branch"]
    switch: Identifier | None = None
    branches: dict[str, dict[Identifier, str]] = Field(default_factory=dict)


class ForEach(Node):
    kind: Literal["foreach"]
    items: str
    max_items: int = Field(gt=0, le=10000)
    on_item_failure: Literal["failed", "review_required", "collect_errors"]
    limits: Limits
    body: "Graph"


class Repeat(Node):
    kind: Literal["repeat_until"]
    initial: InputBinding
    condition: Predicate
    max_iterations: int = Field(gt=0, le=10000)
    on_limit: Literal["failed", "review_required"]
    limits: Limits
    body: "Graph"


GraphNode = Annotated[Task | Review | Switch | Join | ForEach | Repeat, Field(discriminator="kind")]


class Graph(Contract):
    inputs: dict[Identifier, GraphPort]
    # A maximum-size v1 blueprint can have one review per step and a delivery join.
    nodes: list[GraphNode] = Field(min_length=1, max_length=401)
    outputs: dict[Identifier, str] = Field(min_length=1)


class Workflow(Contract):
    id: Identifier
    goal: str = Field(min_length=1)
    revision: int = Field(gt=0)
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")


class Blueprint(Graph):
    blueprint_schema_version: Literal[2]
    graph_contract_version: Literal["2.0", "2.1"] = "2.0"
    workflow: Workflow
    quality_contract: dict[str, JsonValue]
    provenance: dict[str, JsonValue]
    evolution_policy: dict[str, JsonValue] = Field(default_factory=dict)


Graph.model_rebuild()
Blueprint.model_rebuild()
