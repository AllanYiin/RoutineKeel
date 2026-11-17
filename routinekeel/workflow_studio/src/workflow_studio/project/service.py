"""Project edits and revisions are committed together with optimistic concurrency."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from uuid import uuid4

from ..contracts import StudioError
from ..storage.sqlite import SQLiteRepository
from .models import Project, ProjectStatus, Revision, RevisionStatus, canonical_json, validate_name


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectService:
    def __init__(self, repository: SQLiteRepository):
        self.repository = repository

    @staticmethod
    def _project(row) -> Project:
        if row is None:
            raise StudioError("PROJECT_NOT_FOUND", "找不到專案。")
        data = dict(row)
        data["status"] = ProjectStatus(data["status"])
        return Project(**data)

    @staticmethod
    def _revision(row) -> Revision:
        if row is None:
            raise StudioError("REVISION_NOT_FOUND", "找不到版本。")
        data = dict(row)
        data["status"] = RevisionStatus(data["status"])
        data["discarded"] = bool(data["discarded"])
        return Revision(**data)

    def create(self, name: str, goal: dict | None = None) -> Project:
        name, goal_json = validate_name(name), canonical_json({} if goal is None else goal)
        identity, timestamp = "project_" + uuid4().hex, now()
        with self.repository.transaction() as db:
            db.execute(
                "INSERT INTO studio_projects VALUES (?,?,?,'active',1,NULL,?,?)",
                (identity, name, goal_json, timestamp, timestamp),
            )
        return self.get(identity)

    def get(self, project_id: str) -> Project:
        return self._project(
            self.repository.connection.execute(
                "SELECT * FROM studio_projects WHERE project_id=?", (project_id,)
            ).fetchone()
        )

    def list(
        self, *, include_archived: bool = False, include_deleted: bool = False
    ) -> list[Project]:
        statuses = ["active"]
        if include_archived:
            statuses.append("archived")
        if include_deleted:
            statuses.append("deleted")
        placeholders = ",".join("?" for _ in statuses)
        return [
            self._project(row)
            for row in self.repository.connection.execute(
                f"SELECT * FROM studio_projects WHERE status IN ({placeholders}) ORDER BY created_at,project_id",
                statuses,
            )
        ]

    def _editable(self, db, project_id: str, expected_version: int, *, active: bool = True):
        if type(expected_version) is not int or expected_version < 1:
            raise StudioError("INPUT_INVALID", "必須提供有效的預期專案版本。")
        project = self._project(
            db.execute("SELECT * FROM studio_projects WHERE project_id=?", (project_id,)).fetchone()
        )
        if project.version != expected_version:
            raise StudioError("REVISION_CONFLICT", "專案已有更新，請重新開啟後再修改。")
        if active and project.status != ProjectStatus.ACTIVE:
            raise StudioError("PROJECT_INACTIVE", "請先恢復專案後再編輯。")
        return project

    def update(self, project_id: str, expected_version: int, *, name=None, goal=None) -> Project:
        with self.repository.transaction() as db:
            project = self._editable(db, project_id, expected_version)
            db.execute(
                "UPDATE studio_projects SET name=?,goal_json=?,version=version+1,updated_at=? WHERE project_id=?",
                (
                    project.name if name is None else validate_name(name),
                    project.goal_json if goal is None else canonical_json(goal),
                    now(),
                    project_id,
                ),
            )
        return self.get(project_id)

    def set_status(self, project_id: str, expected_version: int, status: ProjectStatus) -> Project:
        try:
            status = ProjectStatus(status)
        except ValueError as exc:
            raise StudioError("INPUT_INVALID", "不支援的專案狀態。") from exc
        with self.repository.transaction() as db:
            project = self._editable(db, project_id, expected_version, active=False)
            if project.status == status:
                return project
            db.execute(
                "UPDATE studio_projects SET status=?,version=version+1,updated_at=? WHERE project_id=?",
                (status.value, now(), project_id),
            )
        return self.get(project_id)

    def archive(self, project_id: str, expected_version: int) -> Project:
        return self.set_status(project_id, expected_version, ProjectStatus.ARCHIVED)

    def delete(self, project_id: str, expected_version: int) -> Project:
        return self.set_status(project_id, expected_version, ProjectStatus.DELETED)

    def restore(self, project_id: str, expected_version: int) -> Project:
        return self.set_status(project_id, expected_version, ProjectStatus.ACTIVE)

    def add_revision(self, project_id: str, expected_version: int, ir: dict) -> Revision:
        return self._commit_revision(project_id, expected_version, ir, "draft")

    def save_ir(self, project_id: str, expected_version: int, payload: dict, tools) -> Revision:
        from ..ir.validator import validate_ir

        versions = {
            (row[0], row[1])
            for row in self.repository.connection.execute(
                """SELECT s.source_id,v.version FROM studio_sources s JOIN studio_source_versions v
            ON s.source_id=v.source_id WHERE s.project_id=?""",
                (project_id,),
            )
        }
        validated = validate_ir(payload, tools, source_versions=versions)
        return self._commit_revision(
            project_id, expected_version, validated.model_dump(), "ir_ready"
        )

    def _commit_revision(self, project_id, expected_version, ir, status):
        payload = canonical_json(ir)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        identity = "revision_" + uuid4().hex
        with self.repository.transaction() as db:
            project = self._editable(db, project_id, expected_version)
            if status == "ir_ready":
                import json

                accepted = [
                    json.loads(row[0])
                    for row in db.execute(
                        "SELECT ir_json FROM studio_revisions WHERE project_id=? AND status<>'draft'",
                        (project_id,),
                    )
                ]
                accepted = [item for item in accepted if item.get("ir_version") == "1.0"]
                if accepted and any(item["workflow_id"] != ir["workflow_id"] for item in accepted):
                    raise StudioError("REVISION_CONFLICT", "同一專案的工作流識別碼必須保持穩定。")
                expected_ir = max((item["revision"] for item in accepted), default=0) + 1
                if ir["revision"] != expected_ir:
                    raise StudioError("REVISION_CONFLICT", f"IR 預期版本為 {expected_ir}。")
            db.execute(
                "INSERT INTO studio_revisions VALUES (?,?,?,?,?,?,0,?)",
                (identity, project_id, project.current_revision_id, payload, digest, status, now()),
            )
            db.execute(
                "UPDATE studio_projects SET current_revision_id=?,version=version+1,updated_at=? WHERE project_id=?",
                (identity, now(), project_id),
            )
        return self.get_revision(project_id, identity)

    def get_revision(self, project_id: str, revision_id: str) -> Revision:
        return self._revision(
            self.repository.connection.execute(
                "SELECT * FROM studio_revisions WHERE project_id=? AND revision_id=?",
                (project_id, revision_id),
            ).fetchone()
        )

    def revisions(self, project_id: str, *, include_discarded: bool = False) -> list[Revision]:
        self.get(project_id)
        return [
            self._revision(row)
            for row in self.repository.connection.execute(
                "SELECT * FROM studio_revisions WHERE project_id=? AND (discarded=0 OR ?) ORDER BY created_at,revision_id",
                (project_id, include_discarded),
            )
        ]

    def discard_revision(self, project_id: str, revision_id: str, expected_version: int) -> Project:
        with self.repository.transaction() as db:
            project = self._editable(db, project_id, expected_version)
            revision = self.get_revision(project_id, revision_id)
            if revision.status not in {RevisionStatus.DRAFT, RevisionStatus.REJECTED}:
                raise StudioError("REVISION_IMMUTABLE", "只能捨棄未發布的草稿或退回版本。")
            if revision.discarded:
                return project
            db.execute(
                "UPDATE studio_revisions SET discarded=1 WHERE revision_id=?", (revision_id,)
            )
            current = project.current_revision_id
            if current == revision_id:
                current = revision.parent_id
                while current:
                    parent = self.get_revision(project_id, current)
                    if not parent.discarded:
                        break
                    current = parent.parent_id
            db.execute(
                "UPDATE studio_projects SET current_revision_id=?,version=version+1,updated_at=? WHERE project_id=?",
                (current, now(), project_id),
            )
        return self.get(project_id)
