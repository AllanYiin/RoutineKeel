import json
from uuid import uuid4

from ..compiler.generator import Compiler
from ..contracts import StudioError
from ..project.service import now
from ..verifier.golden_cases import GoldenCases
from ..verifier.static import verify_static
from .policy import risk_level


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
            if self.studio.projects.get_revision(project_id, build["revision_id"]).discarded:
                raise StudioError("REVISION_IMMUTABLE", "此草稿已捨棄，請先另存新版本並重新驗證。")
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
        for item in report["cases"]:
            current = GoldenCases(self.studio).get(project_id, item["case_id"])
            if current["version"] != item["version"] or current["status"] != "active":
                raise StudioError("TEST_FAILED", "試做案例已更新或封存，請重新驗證。")

    def revoke(self, project_id, approval_id):
        self.get(project_id, approval_id)
        with self.studio.repository.transaction() as db:
            db.execute(
                "UPDATE studio_approvals SET revoked_at=? WHERE approval_id=?", (now(), approval_id)
            )
        return self.get(project_id, approval_id)
