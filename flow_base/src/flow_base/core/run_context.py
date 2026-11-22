"""Run-scoped state; ContextVar prevents cross-thread Runtime contamination."""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any


@dataclass
class RunContext:
    runtime: Any
    run_id: str
    logdir: Path
    reporter: Any
    event_sink: Any | None = None
    job_id: str | None = None
    action: str | None = None
    action_payload: dict | None = None
    attempt_id: str | None = None
    span_id: str | None = field(default_factory=lambda: 'span_' + uuid.uuid4().hex)
    parent_span_id: str | None = None
    cancel_requested: Event = field(default_factory=Event)


ACTIVE_RUN: ContextVar[RunContext | None] = ContextVar('flow_base_run_context', default=None)


@contextmanager
def activate(context: RunContext):
    from ..presentation import ACTIVE

    run_token = ACTIVE_RUN.set(context)
    reporter_token = ACTIVE.set(context.reporter)
    try:
        yield context
    finally:
        ACTIVE.reset(reporter_token)
        ACTIVE_RUN.reset(run_token)


def current(runtime=None) -> RunContext | None:
    context = ACTIVE_RUN.get()
    return context if context is not None and (runtime is None or context.runtime is runtime) else None
