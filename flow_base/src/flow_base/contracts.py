"""Versioned public contracts. No environment access at import time."""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class AssetFile(Contract):
    path: str
    url: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(gt=0)

    @field_validator("path")
    @classmethod
    def safe_path(cls, v):
        p = PurePosixPath(v)
        if p.is_absolute() or ".." in p.parts or "\\" in v or ":" in v or not p.parts:
            raise ValueError("resource path must be a relative POSIX file path")
        return v

    @field_validator("url")
    @classmethod
    def https_only(cls, v):
        p = urlsplit(v)
        if p.scheme != "https" or not p.hostname or p.username or p.password:
            raise ValueError("resource download requires an HTTPS URL without credentials")
        return v


class Resource(Contract):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    revision: str = Field(min_length=1)
    license: str
    files: list[AssetFile] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_files(self):
        names = [f.path for f in self.files]
        if len(names) != len(set(names)):
            raise ValueError("duplicate resource paths")
        return self


class Step(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    handler: str = Field(pattern=r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
    needs: list[str] = Field(default_factory=list)
    parameters: dict = Field(default_factory=dict)
    cacheable: bool | None = None  # None preserves the legacy handler policy.
    side_effects: Literal['none', 'local_write', 'external'] | None = None
    retry_policy: Literal['manual', 'none'] | None = None
    output_artifacts: list[str] = Field(default_factory=list)

    @field_validator('output_artifacts')
    @classmethod
    def safe_output_artifacts(cls, values):
        if len(values) != len(set(values)):
            raise ValueError('Duplicate output artifact path')
        for value in values:
            path = PurePosixPath(value)
            if (not value or value in {'.', '..'} or path.as_posix() != value or
                    path.is_absolute() or '..' in path.parts or '\\' in value or ':' in value):
                raise ValueError('Output artifact paths must be relative POSIX paths')
        return values

    @model_validator(mode='after')
    def side_effect_policy(self):
        if self.side_effects == 'external' and self.cacheable is True:
            raise ValueError('External side effects cannot be cached')
        return self


class LLM(Contract):
    enabled: bool = False
    endpoint: str = "https://api.openai.com/v1/chat/completions"
    model: str = ""
    api_key_env: str = "FLOW_BASE_API_KEY"
    # Decimal money stored as integer micro-USD in the ledger.
    budget_usd: float = Field(default=0.25, gt=0, le=1000)
    input_usd_per_million: float = Field(default=0, ge=0)
    output_usd_per_million: float = Field(default=0, ge=0)
    pricing_verified: bool = False
    pricing_note: str = ""
    max_calls: int = Field(default=20, ge=1, le=10000)
    max_output_tokens: int = Field(default=1200, ge=64, le=16000)
    batch_chars: int = Field(default=1600, ge=100, le=20000)
    context_segments: int = Field(default=1, ge=0, le=3)
    timeout_seconds: int = Field(default=60, ge=5, le=300)
    max_edit_ratio: float = Field(default=0.35, ge=0, le=1)

    @model_validator(mode="after")
    def validate_provider(self):
        u = urlsplit(self.endpoint)
        if u.scheme != "https" or not u.hostname or u.username or u.password or u.query:
            raise ValueError("LLM endpoint must be HTTPS without credentials/query")
        if self.enabled and (not self.model or not self.pricing_verified or not self.pricing_note
                             or self.input_usd_per_million <= 0 or self.output_usd_per_million <= 0):
            raise ValueError("enabled LLM needs model, verified positive rates, and pricing_note")
        return self


class Workflow(Contract):
    schema_version: Literal[1] = 1
    runtime: Literal['0.2'] = '0.2'
    id: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,63}$')
    steps: list[Step] = Field(min_length=1)
    options: Contract = Field(default_factory=Contract)
    outputs: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def graph(self):
        seen = set()
        for step in self.steps:
            if step.id in seen or not set(step.needs) <= seen:
                raise ValueError('Steps must have unique IDs in dependency order')
            seen.add(step.id)
        if self.outputs:
            nonterminal = {dependency for step in self.steps for dependency in step.needs}
            terminals = seen - nonterminal
            for name, step_id in self.outputs.items():
                if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', name) or name in {'.', '..'}:
                    raise ValueError('Workflow output names must be safe identifiers')
                if step_id not in terminals:
                    raise ValueError('Workflow outputs must reference terminal steps')
        return self


