"""Immutable snapshots, not live database entities."""

import json
from dataclasses import dataclass
from enum import StrEnum

from ..contracts import StudioError


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    DELETED = "deleted"


class RevisionStatus(StrEnum):
    DRAFT = "draft"
    DISCOVERING = "discovering"
    NEEDS_CLARIFICATION = "needs_clarification"
    IR_READY = "ir_ready"
    GENERATING = "generating"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    REJECTED = "rejected"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PUBLISHED = "published"


def canonical_json(value: dict) -> str:
    if not isinstance(value, dict):
        raise StudioError("INPUT_INVALID", "內容必須是 JSON 物件。")
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError, RecursionError) as exc:
        raise StudioError("INPUT_INVALID", "內容含有不支援的 JSON 值。") from exc


def validate_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise StudioError("INPUT_INVALID", "專案名稱須為 1 至 200 字。")
    return name.strip()


@dataclass(frozen=True)
class Project:
    project_id: str
    name: str
    goal_json: str
    status: ProjectStatus
    version: int
    current_revision_id: str | None
    created_at: str
    updated_at: str

    @property
    def goal(self) -> dict:
        return json.loads(self.goal_json)


@dataclass(frozen=True)
class Revision:
    revision_id: str
    project_id: str
    parent_id: str | None
    ir_json: str
    ir_hash: str
    status: RevisionStatus
    discarded: bool
    created_at: str

    @property
    def ir(self) -> dict:
        return json.loads(self.ir_json)
