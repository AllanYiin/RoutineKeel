import hashlib

from pydantic import Field, field_validator

from ..ir.models import Resources, ToolSignature
from ..project.models import canonical_json


class ToolDescription(ToolSignature):
    title: str = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)
    resources: Resources
    permissions: list[str]
    implementation: str
    test_evidence: list[str]
    cost_micro_usd: int = Field(ge=0)

    @field_validator("tool_id")
    @classmethod
    def safe_tool_id(cls, value):
        import re

        if not re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", value):
            raise ValueError("Invalid tool ID")
        return value

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(canonical_json(self.model_dump()).encode()).hexdigest()

    def signature(self) -> ToolSignature:
        return ToolSignature.model_validate(
            {name: getattr(self, name) for name in ToolSignature.model_fields}
        )
