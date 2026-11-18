import json
from uuid import uuid4

from ..compiler.generator import Compiler
from ..contracts import StudioError
from ..project.service import now
from ..verifier.golden_cases import GoldenCases
from ..verifier.static import verify_static
from .policy import risk_level
from .reviews import ReviewStore


class Approvals:
    def __init__(self, studio):
        self.studio = studio

    def approve(
        self,
        project_id,
        build_id,
        expected_hash,
        policy,
        *,
        actor,
        reason,
        external_authorized=False,
    ):
        if (
            not isinstance(actor, str)
            or not actor.strip()
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise StudioError("APPROVAL_REQUIRED", "請記錄核准者與確認理由。")
        build = Compiler(self.studio).get(project_id, build_id)
        review = ReviewStore(self.studio.repository)
        review.assert_open(project_id, build_id, for_approval=True)
        if build["package_hash"] != expected_hash or build["status"] != "verified":
            raise StudioError("TEST_FAILED", "套件尚未通過驗證，或核准指紋已改變。")
        verify_static(self.studio, build, policy)
        revision = self.studio.projects.get_revision(project_id, build["revision_id"])
        risk = risk_level(revision.ir)
        if risk == "high" and not external_authorized:
            raise StudioError("APPROVAL_REQUIRED", "模型或外部操作須明確核准範圍與預算。")
        reports = self.studio.repository.connection.execute(
            "SELECT report_id,report_json FROM studio_verifications WHERE project_id=? AND build_id=? ORDER BY created_at DESC",
            (project_id, build_id),
        ).fetchall()
        report = next(
            (
                r
                for r in reports
                if json.loads(r[1]).get("status") == "passed"
                and json.loads(r[1]).get("package_hash") == expected_hash
            ),
            None,
        )
        if report is None:
            raise StudioError("TEST_FAILED", "缺少符合套件指紋的獨立通過報告。")
        self.validate_cases(project_id, json.loads(report[1]))
        identity = "approval_" + uuid4().hex
        with self.studio.repository.transaction() as db:
            if self.studio.projects.get(project_id).status != "active":
                raise StudioError("PROJECT_INACTIVE", "請先恢復專案後再核准新建置。")
            review.assert_open(project_id, build_id, for_approval=True)
            verify_static(self.studio, build, policy)
            self.validate_cases(project_id, json.loads(report[1]))
            if self.studio.projects.get_revision(project_id, build["revision_id"]).discarded:
                raise StudioError("REVISION_IMMUTABLE", "此草稿已捨棄，請先另存新版本並重新驗證。")
            existing = db.execute(
                "SELECT approval_id FROM studio_approvals WHERE project_id=? AND build_id=? AND package_hash=? "
                "AND report_id=? AND actor=? AND reason=? AND external_authorized=? AND revoked_at IS NULL ORDER BY created_at DESC LIMIT 1",
                (
                    project_id,
                    build_id,
                    expected_hash,
                    report[0],
                    actor.strip(),
                    reason.strip(),
                    int(external_authorized),
                ),
            ).fetchone()
            if existing:
                return self.get(project_id, existing[0])
            db.execute(
                "INSERT INTO studio_approvals VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)",
                (
                    identity,
                    project_id,
                    build_id,
                    build["revision_id"],
                    expected_hash,
                    report[0],
                    risk,
                    actor.strip(),
                    reason.strip(),
                    int(external_authorized),
                    now(),
                ),
            )
            review.record(
                project_id,
                build_id,
                expected_hash,
                "approve",
                actor=actor.strip(),
                reason=reason.strip(),
                report_id=report[0],
                approval_id=identity,
            )
        return self.get(project_id, identity)

    def get(self, project_id, approval_id):
        row = self.studio.repository.connection.execute(
            "SELECT * FROM studio_approvals WHERE project_id=? AND approval_id=?",
            (project_id, approval_id),
        ).fetchone()
        if row is None:
            raise StudioError("APPROVAL_REQUIRED", "找不到核准紀錄。")
        return dict(row)

    def validate_cases(self, project_id, report):
        catalog = GoldenCases(self.studio)
        if {case["case_id"] for case in catalog.list(project_id)} != {
            item["case_id"] for item in report["cases"]
        }:
            raise StudioError("TEST_FAILED", "成功案例清單已改變或未全部驗收，請重新驗證。")
        for item in report["cases"]:
            current = catalog.get(project_id, item["case_id"])
            if current["version"] != item["version"] or current["status"] != "active":
                raise StudioError("TEST_FAILED", "試做案例已更新或封存，請重新驗證。")
            for side in ("input", "expected"):
                ref = current["specification"][side]
                source = self.studio.sources.get(project_id, ref["source_id"])
                if source.status != "active" or source.version != ref["version"]:
                    raise StudioError(
                        "TEST_FAILED", "案例來源已更新或移除，請更新案例來源版本並重新驗證。"
                    )
            catalog.bytes(project_id, current["case_id"], version=current["version"])

    def revoke(self, project_id, approval_id, *, actor=None, reason=None):
        from ..registry.catalog import Catalog

        Catalog._reviewer(actor, reason)
        with self.studio.repository.transaction() as db:
            approval = self.get(project_id, approval_id)
            if approval["revoked_at"]:
                return approval
            db.execute(
                "UPDATE studio_approvals SET revoked_at=? WHERE approval_id=?", (now(), approval_id)
            )
            ReviewStore(self.studio.repository).record(
                project_id,
                approval["build_id"],
                approval["package_hash"],
                "revoke",
                actor=actor.strip() if actor else None,
                reason=reason.strip() if reason else None,
                report_id=approval["report_id"],
                approval_id=approval_id,
            )
        return self.get(project_id, approval_id)
