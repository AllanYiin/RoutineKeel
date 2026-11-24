"""Portable requirement, observed implementation and selected environment contracts."""

from typing import Literal

from pydantic import Field, JsonValue

from .domain_assets import (
    AssetID,
    AssetRef,
    AssetVersion,
    CapabilityPort,
    Fingerprint,
    Locus,
    content_hash,
)
from .models import Contract, Identifier


class ImplementationAdapter(Contract):
    id: AssetID
    version: AssetVersion
    capability: AssetID
    capability_version: AssetVersion
    provider_version: str = Field(min_length=1, max_length=200)
    model_version: str | None = Field(default=None, min_length=1, max_length=200)
    locus: Locus
    tool: str = Field(min_length=1, max_length=200)
    distribution: str | None = Field(default=None, min_length=1, max_length=200)
    inputs: dict[Identifier, CapabilityPort]
    outputs: dict[Identifier, CapabilityPort]
    permissions: list[str]
    side_effect: Literal["none", "local_write", "external"]
    network: bool
    platforms: list[Literal["win32", "linux", "darwin"]] = Field(min_length=1)
    max_cost_micro_usd: int = Field(ge=0)
    max_latency_ms: int | None = Field(default=None, ge=1)
    max_data_age_seconds: int | None = Field(default=None, ge=0)
    numeric_representation: Literal["decimal", "integer", "binary_float"] | None = None
    error_codes: list[Identifier] = Field(default_factory=list)
    contract_hash: Fingerprint | None = None

    @property
    def ref(self):
        return f"{self.id}@{self.version}"


class EnvironmentBinding(Contract):
    requirement_id: Identifier
    capability: AssetRef
    adapter: AssetRef
    execution_locus: Locus
    provider_version: str = Field(min_length=1, max_length=200)
    model_version: str | None = None
    permission_scope: list[str]
    schema_hash: Fingerprint
    implementation_hash: Fingerprint
    environment_fingerprint: Fingerprint
    qualification_receipt: Fingerprint
    toolchain_plan: dict[str, JsonValue] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class EnvironmentLock(Contract):
    binding_schema_version: Literal["1.0"]
    workflow: str = Field(min_length=1, max_length=200)
    graph_hash: Fingerprint
    blueprint_hash: Fingerprint
    pattern_refs: list[AssetRef]
    bindings: dict[str, EnvironmentBinding] = Field(min_length=1)

    @property
    def lock_hash(self):
        return content_hash(self.model_dump(mode="json"))
