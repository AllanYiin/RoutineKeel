"""Explicit span identifiers; context propagation never assumes new threads inherit them."""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class Span:
    span_id: str
    parent_span_id: str | None


ACTIVE_SPAN: ContextVar[Span | None] = ContextVar('flow_base_span', default=None)


@contextmanager
def span(parent_span_id=None):
    parent = ACTIVE_SPAN.get()
    value = Span('span_' + uuid.uuid4().hex, parent_span_id or (parent.span_id if parent else None))
    token = ACTIVE_SPAN.set(value)
    try:
        yield value
    finally:
        ACTIVE_SPAN.reset(token)


def current_span():
    return ACTIVE_SPAN.get()
