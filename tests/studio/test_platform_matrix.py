import hashlib
import platform
from uuid import UUID

from compiler_helpers import build_ir, execute_package
from gate_evidence import write_evidence
from workflow_studio.registry.policy import ToolPolicy


def test_same_canonical_ir_has_platform_reports_and_runs_on_actual_host(tmp_path, monkeypatch):
    # A fixed source identity makes the IR fingerprint comparable across isolated CI jobs.
    with monkeypatch.context() as patch:
        patch.setattr("workflow_studio.ingestion.service.uuid4", lambda: UUID(int=1))
        studio, p, revision, _ = build_ir(tmp_path)
    try:
        builds = {
            target: studio.compile(p.project_id, revision.revision_id, ToolPolicy(target))
            for target in ("windows", "macos", "linux")
        }
        assert len({build["mapping"]["ir_hash"] for build in builds.values()}) == 1
        assert len({build["package_hash"] for build in builds.values()}) == 1
        host = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}[platform.system()]
        content = "fixed platform input · 中文\n".encode()
        report = execute_package(builds[host]["path"] / "package", tmp_path / "runtime", content)
        actual = (tmp_path / "runtime/output/artifacts/result/artifacts/result.txt").read_bytes()
        assert actual == content and report["token_usage"]["registered_requests"] == 0
        write_evidence(
            tmp_path,
            "platform-contract",
            {
                "ir_sha256": revision.ir_hash,
                "policy_validation_targets": list(builds),
                "actually_executed_host": host,
                "actual_output_sha256": hashlib.sha256(actual).hexdigest(),
                "run_id": report["run_id"],
                "package_hash_on_this_host": builds[host]["package_hash"],
                "passed": True,
                "scope": "Only the actual host was executed; other policies are static checks, not platform acceptance",
            },
        )
    finally:
        studio.close()
