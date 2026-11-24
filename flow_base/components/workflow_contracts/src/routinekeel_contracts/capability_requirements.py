"""Tool-independent capability requirements used by Graph and Domain Packs."""

from typing import Annotated, Literal

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion
from pydantic import Field, model_validator

from .models import Contract, Identifier

_NAME = r"[a-z][a-z0-9_-]{0,63}(?:\.[a-z][a-z0-9_-]{0,63}){1,7}"
_VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
AssetID = Annotated[str, Field(pattern=f"^{_NAME}$")]
AssetVersion = Annotated[str, Field(pattern=f"^{_VERSION}$", max_length=40)]
AssetRef = Annotated[str, Field(pattern=f"^{_NAME}@{_VERSION}$", max_length=560)]
Fingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Locus = Literal["local_runtime", "host_mediated", "external_service"]


def unique(values):
    if len(set(values)) != len(values):
        raise ValueError("資產宣告含重複識別碼")


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
    version_range: str | None = Field(
        default=None, min_length=1, max_length=120, exclude_if=lambda value: value is None
    )
    input_semantics: dict[Identifier, AssetID] = Field(
        default_factory=dict, exclude_if=lambda value: not value
    )
    output_semantics: dict[Identifier, AssetID] = Field(
        default_factory=dict, exclude_if=lambda value: not value
    )
    allowed_destinations: list[Annotated[str, Field(min_length=1, max_length=256)]] = Field(
        default_factory=list, max_length=32, exclude_if=lambda value: not value
    )

    @model_validator(mode="after")
    def binding_policy(self):
        unique(self.execution_loci)
        if (self.portability == "pinned") != (self.pinned_adapter is not None):
            raise ValueError("固定依賴須有具版本的 Adapter")
        if self.required and self.fallback_contract is not None:
            raise ValueError("必要能力不得宣告省略交付")
        unique(self.allowed_destinations)
        if self.version_range is not None:
            try:
                specifier = SpecifierSet(self.version_range)
            except InvalidSpecifier as exc:
                raise ValueError("能力版本範圍無效") from exc
            if not specifier or self.version is not None:
                raise ValueError("請擇一宣告固定能力版本或版本範圍")
        return self

    def accepts_version(self, version):
        if version.isdecimal():
            version += ".0.0"
        if self.version_range:
            try:
                return version in SpecifierSet(self.version_range)
            except InvalidVersion:
                return False
        return self.version in {None, version}
