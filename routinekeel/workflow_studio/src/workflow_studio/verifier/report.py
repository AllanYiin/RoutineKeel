import json
import sys
from pathlib import Path
from uuid import uuid4

from ..compiler.generator import Compiler
from ..compiler.package import digest
from ..contracts import StudioError
from ..project.models import canonical_json
from ..project.service import now
from .golden_cases import GoldenCases
from .replay import output_bytes, replay
from .static import verify_static


class Verifier:
    def __init__(self, studio, runtime_python=None):
        self.studio = studio
        self.runtime_python = Path(runtime_python or sys.executable).resolve()

    def verify(self, project_id, build_id, cases, policy):
        build = Compiler(self.studio).get(project_id, build_id)
        report_id = "verification_" + uuid4().hex
        folder = build["path"] / "verification" / report_id
        report = {
            "report_id": report_id,
            "project_id": project_id,
            "build_id": build_id,
            "package_hash": build["package_hash"],
            "status": "failed",
            "static": "not_run",
            "cases": [],
            "created_at": now(),
        }
        try:
            manifest, package_files = verify_static(self.studio, build, policy)
            report["static"] = "passed"
            if not cases or {c["output"] for c in cases} != set(manifest["outputs"]):
                raise StudioError("TEST_FAILED", "Golden Cases 必須涵蓋全部具名交付物。")
            revision = self.studio.projects.get_revision(project_id, build["revision_id"])
            catalog = GoldenCases(self.studio)
            for i, binding in enumerate(cases):
                case = catalog.get(project_id, binding["case_id"], binding["version"])
                if case["status"] != "active":
                    raise StudioError("TEST_FAILED", "Golden Case 已封存。")
                source, expected = catalog.bytes(
                    project_id, case["case_id"], version=case["version"]
                )
                result = {
                    "case_id": case["case_id"],
                    "version": case["version"],
                    "output": binding["output"],
                    "acceptance_hash": digest(canonical_json(case["specification"]).encode()),
                    "input_sha256": digest(source),
                    "expected_sha256": digest(expected),
                    "status": "failed",
                }
                report["cases"].append(result)
                root = folder / f"case-{i + 1}"
                runtime = replay(package_files, root, source, self.runtime_python)
                actual = output_bytes(root, runtime, binding["output"], revision.ir)
                result.update(
                    actual_sha256=digest(actual),
                    run_id=runtime["run_id"],
                    lifecycle=runtime["lifecycle"],
                )
                if actual != expected:
                    result["difference"] = {
                        "expected_bytes": len(expected),
                        "actual_bytes": len(actual),
                    }
                    raise StudioError("TEST_FAILED", "實際成果與固定成功案例不符。")
                result["status"] = "passed"
            report["status"] = "passed"
        except (StudioError, OSError, ValueError, KeyError) as exc:
            report["error_code"] = exc.code if isinstance(exc, StudioError) else "TEST_FAILED"
            report["message"] = (
                str(exc) if isinstance(exc, StudioError) else "測試資料或執行環境不完整。"
            )
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "report.json").write_text(canonical_json(report), encoding="utf-8")
        with self.studio.repository.transaction() as db:
            db.execute(
                "INSERT INTO studio_verifications VALUES (?,?,?,?,?)",
                (
                    report_id,
                    project_id,
                    build_id,
                    canonical_json(report),
                    now(),
                ),
            )
            db.execute(
                "UPDATE studio_builds SET status=? WHERE build_id=?",
                ("verified" if report["status"] == "passed" else "rejected", build_id),
            )
        return report

    def get(self, project_id, report_id):
        row = self.studio.repository.connection.execute(
            "SELECT report_json FROM studio_verifications WHERE project_id=? AND report_id=?",
            (project_id, report_id),
        ).fetchone()
        if row is None:
            raise StudioError("TEST_FAILED", "找不到驗證報告。")
        return json.loads(row[0])
