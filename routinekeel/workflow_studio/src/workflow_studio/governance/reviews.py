"""Review decisions are separate from technical test results and never erase artifacts."""

import json
from uuid import uuid4

from ..contracts import StudioError
from ..project.service import now


class ReviewStore:
    def __init__(self, repository):
        self.repository = repository

    def build(self, project_id, build_id):
        row = self.repository.connection.execute(
            "SELECT * FROM studio_builds WHERE project_id=? AND build_id=?", (project_id, build_id)
        ).fetchone()
        if row is None:
            raise StudioError("BUILD_NOT_FOUND", "找不到此專案的建置。")
        return dict(row)

    def history(self, project_id, build_id):
        self.build(project_id, build_id)
        return [
            dict(r)
            for r in self.repository.connection.execute(
                "SELECT * FROM studio_build_reviews WHERE project_id=? AND build_id=? ORDER BY sequence",
                (project_id, build_id),
            )
        ]

    def state(self, project_id, build_id):
        records = self.history(project_id, build_id)
        build = self.build(project_id, build_id)
        archived, returned, returned_report, returned_sequence = False, False, None, 0
        for record in records:
            if record["action"] in {"archive", "restore"}:
                archived = record["action"] == "archive"
        revision_review = self.repository.connection.execute(
            "SELECT r.* FROM studio_build_reviews r JOIN studio_builds b ON b.build_id=r.build_id "
            "WHERE r.project_id=? AND b.revision_id=? AND r.action IN ('submit','return') ORDER BY r.sequence DESC LIMIT 1",
            (project_id, build["revision_id"]),
        ).fetchone()
        if revision_review:
            returned = revision_review["action"] == "return"
            if returned:
                returned_report, returned_sequence = (
                    revision_review["report_id"],
                    revision_review["sequence"],
                )
        return {
            "sequence": max(
                records[-1]["sequence"] if records else 0,
                revision_review["sequence"] if revision_review else 0,
            ),
            "archived": archived,
            "returned": returned,
            "returned_report_id": returned_report,
            "returned_sequence": returned_sequence,
            "revision_review": dict(revision_review) if revision_review else None,
        }

    def assert_open(self, project_id, build_id, *, for_approval=False):
        state = self.state(project_id, build_id)
        if state["archived"]:
            raise StudioError("BUILD_ARCHIVED", "此建置已封存；請恢復或建立新的候選包。")
        if for_approval and state["returned"]:
            raise StudioError("REVIEW_RETURNED", "此建置已被退回；請重新試做後送審，或建立新版本。")
        return state

    def record(
        self,
        project_id,
        build_id,
        package_hash,
        action,
        *,
        actor=None,
        reason=None,
        report_id=None,
        approval_id=None,
    ):
        build = self.build(project_id, build_id)
        if build["package_hash"] != package_hash:
            raise StudioError("REVISION_CONFLICT", "建置指紋已變動。")
        identity = "review_" + uuid4().hex
        self.repository.connection.execute(
            "INSERT INTO studio_build_reviews(review_id,project_id,build_id,package_hash,action,report_id,approval_id,actor,reason,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                identity,
                project_id,
                build_id,
                package_hash,
                action,
                report_id,
                approval_id,
                actor,
                reason,
                now(),
            ),
        )
        return identity


class BuildReviews:
    def __init__(self, studio, policy):
        self.studio, self.policy = studio, policy
        self.store = ReviewStore(studio.repository)

    def list(self, project_id, *, include_archived=False):
        self.studio.projects.get(project_id)
        builds = []
        for row in self.studio.repository.connection.execute(
            "SELECT build_id FROM studio_builds WHERE project_id=? ORDER BY created_at,build_id",
            (project_id,),
        ):
            item = self.details(project_id, row[0], reports=False)
            if include_archived or not item["review"]["archived"]:
                builds.append(item)
        return builds

    def details(self, project_id, build_id, *, reports=True):
        from ..compiler.generator import Compiler

        build = Compiler(self.studio).get(project_id, build_id)
        approvals = [
            dict(r)
            for r in self.studio.repository.connection.execute(
                "SELECT * FROM studio_approvals WHERE project_id=? AND build_id=? ORDER BY created_at,approval_id",
                (project_id, build_id),
            )
        ]
        result = {
            "build": build,
            "review": self.store.state(project_id, build_id),
            "approvals": approvals,
            "history": self.store.history(project_id, build_id),
            "release_references": [
                dict(r)
                for r in self.studio.repository.connection.execute(
                    "SELECT release_id,status,target_root FROM studio_releases WHERE project_id=? AND build_id=? ORDER BY created_at",
                    (project_id, build_id),
                )
            ],
        }
        if reports:
            result["reports"] = [
                json.loads(r[0])
                for r in self.studio.repository.connection.execute(
                    "SELECT report_json FROM studio_verifications WHERE project_id=? AND build_id=? ORDER BY created_at,report_id",
                    (project_id, build_id),
                )
            ]
        return result

    def decide(
        self,
        project_id,
        expected_version,
        build_id,
        expected_hash,
        expected_sequence,
        action,
        *,
        actor,
        reason,
    ):
        from ..registry.catalog import Catalog
        from ..verifier.static import verify_static
        from .approvals import Approvals

        Catalog._reviewer(actor, reason, required=True)
        if type(expected_sequence) is not int or expected_sequence < 0:
            raise StudioError("INPUT_INVALID", "審核版本必須是非負整數。")
        if action not in {"submit", "return", "archive", "restore"}:
            raise StudioError("INPUT_INVALID", "審核操作不合法。")
        with self.studio.repository.transaction() as db:
            self.studio.projects._editable(db, project_id, expected_version)
            details = self.details(project_id, build_id)
            build, state = details["build"], details["review"]
            if build["package_hash"] != expected_hash or state["sequence"] != expected_sequence:
                raise StudioError("REVISION_CONFLICT", "建置或審核紀錄已改變，請重新讀取。")
            latest = details["reports"][-1] if details["reports"] else None
            if action == "restore":
                if not state["archived"]:
                    raise StudioError("INPUT_INVALID", "此建置未封存。")
            else:
                self.store.assert_open(project_id, build_id)
            if action == "submit":
                if build["status"] != "verified" or latest is None or latest["status"] != "passed":
                    raise StudioError("TEST_FAILED", "請先完成通過的獨立試做。")
                if (
                    state["returned"]
                    and latest.get("review_sequence", 0) < state["returned_sequence"]
                ):
                    raise StudioError("TEST_FAILED", "退回後必須有新的試做報告才能再次送審。")
                verify_static(self.studio, build, self.policy)
                Approvals(self.studio).validate_cases(project_id, latest)
            if action == "archive":
                if any(a["revoked_at"] is None for a in details["approvals"]):
                    raise StudioError("APPROVAL_REQUIRED", "請先撤回此建置的有效核准，再封存。")
                if db.execute(
                    "SELECT 1 FROM studio_releases WHERE project_id=? AND build_id=?",
                    (project_id, build_id),
                ).fetchone():
                    raise StudioError(
                        "REVISION_IMMUTABLE",
                        "建置已有發布或準備紀錄，請先核對發布狀態；不能直接封存。",
                    )
            if action == "return":
                db.execute(
                    "UPDATE studio_approvals SET revoked_at=? WHERE project_id=? AND revision_id=? AND revoked_at IS NULL",
                    (now(), project_id, build["revision_id"]),
                )
            self.store.record(
                project_id,
                build_id,
                expected_hash,
                action,
                actor=actor.strip(),
                reason=reason.strip(),
                report_id=latest["report_id"] if latest else None,
            )
            db.execute(
                "UPDATE studio_projects SET version=version+1,updated_at=? WHERE project_id=?",
                (now(), project_id),
            )
        return self.details(project_id, build_id)
