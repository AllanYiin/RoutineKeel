"""Strict schema: no inferred fields, coercion, executable expressions or raw paths."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from .paths import safe_relative

Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
NodeId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")]
Version = Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
Platform = Literal["windows", "macos", "linux"]
Kind = Literal["deterministic", "model_inference", "llm", "human_approval", "composite_branch"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Port(Contract):
    type: Literal["file", "string", "integer", "number", "boolean", "object", "array"]
    required: bool = True
    format: str | None = None
    path: str | None = None
    schema_id: str | None = None

    @model_validator(mode="after")
    def file_path(self):
        if self.path is not None:
            if self.type != "file":
                raise ValueError("Only file ports have artifact paths")
            safe_relative(self.path)
        return self


class Binding(Contract):
    source: str
    port: Port


class Condition(Contract):
    operator: Literal["exists", "nonempty", "equals", "sha256"]
    reference: str
    value: JsonValue = None

    @model_validator(mode="after")
    def condition_value(self):
        if self.operator == "sha256":
            import re

            if not isinstance(self.value, str) or not re.fullmatch(r"[a-f0-9]{64}", self.value):
                raise ValueError("sha256 condition needs a lowercase SHA-256")
        return self


class EvidenceRef(Contract):
    id: Identifier
    source_id: Annotated[str, Field(min_length=1)]
    source_version: Annotated[int, Field(ge=1)]
    evidence_locator: Annotated[str, Field(min_length=1)]
    claim: Annotated[str, Field(min_length=1)]
    confidence_label: Literal["observed", "inferred", "verified", "human_confirmed", "conflicted"]
    verified_by: str | None = None

    @model_validator(mode="after")
    def verified_requires_record(self):
        if self.confidence_label in {"verified", "human_confirmed"} and not self.verified_by:
            raise ValueError("Confirmed evidence requires an auditable verification record")
        return self


class RetryPolicy(Contract):
    mode: Literal["never", "manual", "transient_only"]
    max_attempts: Annotated[int, Field(ge=1, le=3)]

    @model_validator(mode="after")
    def never_retries(self):
        if self.mode == "never" and self.max_attempts != 1:
            raise ValueError("never means exactly one attempt")
        return self


class CachePolicy(Contract):
    mode: Literal["none", "content_addressed"]
    fingerprint_fields: list[Literal["inputs", "parameters", "tool_version"]]

    @model_validator(mode="after")
    def complete_fingerprint(self):
        if self.mode == "content_addressed" and set(self.fingerprint_fields) != {
            "inputs",
            "parameters",
            "tool_version",
        }:
            raise ValueError("Cache fingerprint requires inputs, parameters and tool_version")
        return self


class LLMPolicy(Contract):
    model: Annotated[str, Field(min_length=1)]
    api_key: str
    max_calls: Annotated[int, Field(ge=1, le=100)]
    max_output_tokens: Annotated[int, Field(ge=1, le=16000)]
    budget_micro_usd: Annotated[int, Field(gt=0)]
    timeout_seconds: Annotated[int, Field(ge=1, le=300)]


class Resources(Contract):
    memory_mb: Annotated[int, Field(ge=0)]
    disk_mb: Annotated[int, Field(ge=0)]
    network: bool


class Node(Contract):
    id: NodeId
    kind: Kind
    tool: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")]
    tool_version: Version
    inputs: dict[Identifier, Binding]
    outputs: dict[Identifier, Port]
    depends_on: list[NodeId]
    parameters: dict[str, JsonValue]
    preconditions: list[Condition]
    postconditions: Annotated[list[Condition], Field(min_length=1)]
    side_effects: Literal["none", "local_write", "external"]
    retry_policy: RetryPolicy
    cache_policy: CachePolicy
    platform_requirements: Annotated[list[Platform], Field(min_length=1)]
    resources: Resources
    timeout_seconds: Annotated[int, Field(ge=1, le=3600)]
    evidence_refs: Annotated[list[Identifier], Field(min_length=1)]
    llm_policy: LLMPolicy | None = None

    @model_validator(mode="after")
    def execution_policies(self):
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("Duplicate dependencies")
        if self.side_effects == "external" and (
            self.cache_policy.mode != "none" or self.retry_policy.max_attempts != 1
        ):
            raise ValueError("External effects cannot be cached or automatically retried")
        if (self.kind == "llm") != (self.llm_policy is not None):
            raise ValueError("Only llm nodes require LLM policy")
        if not self.outputs:
            raise ValueError("Every node declares outputs")
        paths = []
        for output in self.outputs.values():
            if output.type == "file":
                if not output.path:
                    raise ValueError("File output needs a controlled relative path")
                paths.append(output.path.casefold())
        if len(paths) != len(set(paths)):
            raise ValueError("File outputs have colliding paths")
        return self


class Goal(Contract):
    description: Annotated[str, Field(min_length=1)]
    success_criteria: Annotated[list[str], Field(min_length=1)]

    @field_validator("success_criteria")
    @classmethod
    def nonblank(cls, value):
        if not all(x.strip() for x in value):
            raise ValueError("Success criteria cannot be blank")
        return value


class WorkflowIR(Contract):
    ir_version: Literal["1.0"]
    workflow_id: Identifier
    revision: Annotated[int, Field(ge=1)]
    goal: Goal
    inputs: dict[Identifier, Port]
    secrets: list[Identifier]
    nodes: Annotated[list[Node], Field(min_length=1, max_length=200)]
    outputs: dict[Identifier, str]
    evidence: list[EvidenceRef]


class ToolSignature(Contract):
    """Registry-provided contracts; separate from model-generated node proposals."""

    tool_id: str
    version: Version
    kind: Kind
    inputs: dict[Identifier, Port]
    outputs: dict[Identifier, Port]
    platforms: list[Platform]
    side_effects: Literal["none", "local_write", "external"]
