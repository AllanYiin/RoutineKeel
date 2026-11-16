"""Versioned immutable request and response envelopes for the local service."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenDict(dict):
    def _readonly(self, *_args, **_kwargs):
        raise TypeError('Service request/response values are immutable')

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = _readonly


def _freeze(value):
    if isinstance(value, dict):
        return FrozenDict({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


class Immutable(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class PlanRequest(Immutable):
    task: str = Field(min_length=1)
    source: Path | None = None
    output: Path | None = None
    overrides: dict = Field(default_factory=dict)
    offline: bool = False
    allow_external_paths: bool = False

    @model_validator(mode='after')
    def deep_freeze(self):
        object.__setattr__(self, 'overrides', _freeze(self.overrides))
        return self


class StartRequest(PlanRequest):
    source: Path
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    expected_spec_hash: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')


class StartResult(Immutable):
    contract_version: Literal['1.0'] = '1.0'
    run_id: str
    status: str
    submitted_at: str
    execution_spec_hash: str


class PlanResult(Immutable):
    contract_version: Literal['1.0'] = '1.0'
    plan: dict
    execution_spec_hash: str | None = None
    flow_id_status: Literal['registered', 'provisional']
    estimates_status: Literal['declared', 'unknown']

    @model_validator(mode='after')
    def deep_freeze(self):
        object.__setattr__(self, 'plan', _freeze(self.plan))
        return self


class ServiceError(Immutable):
    """Adapter-neutral, scrubbed error fields for local clients."""
    contract_version: Literal['1.0'] = '1.0'
    code: str
    message: str
    error_id: str | None = None
    details: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def deep_freeze(self):
        object.__setattr__(self, 'details', _freeze(self.details))
        return self
