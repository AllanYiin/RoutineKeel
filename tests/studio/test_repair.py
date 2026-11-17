import pytest
from test_verifier import verification_case
from workflow_studio.contracts import StudioError
from workflow_studio.verifier.repair import RepairLoop


def test_two_failed_repairs_stop_without_changing_acceptance(tmp_path):
    studio, p, build, cases, verifier, policy = verification_case(tmp_path, expected="unreachable")
    calls = []

    def propose(report, original):
        calls.append(report["report_id"])
        return original

    result = RepairLoop(studio, verifier).run(
        p.project_id, build["build_id"], cases, policy, propose
    )
    assert result["status"] == "failed"
    assert result["repair_rounds"] == 2
    assert len(calls) == 2
    assert len({r["build_id"] for r in result["reports"]}) == 3
    hashes = {r["cases"][0]["acceptance_hash"] for r in result["reports"]}
    assert len(hashes) == 1
    studio.close()


def test_repair_rejects_changed_success_conditions(tmp_path):
    studio, p, build, cases, verifier, policy = verification_case(tmp_path, expected="wrong")

    def propose(report, original):
        original["goal"]["success_criteria"] = ["不用檢查內容"]
        return original

    with pytest.raises(StudioError, match="成功條件"):
        RepairLoop(studio, verifier).run(p.project_id, build["build_id"], cases, policy, propose)
    studio.close()


def test_successful_repair_creates_new_version_and_retests(tmp_path):
    from ir_cases import valid_ir
    from workflow_studio.compiler.generator import Compiler

    payload = valid_ir()
    payload["nodes"][0]["postconditions"] = [
        {"operator": "sha256", "reference": "artifact://copy/result", "value": "0" * 64}
    ]
    studio, p, build, cases, verifier, policy = verification_case(tmp_path, payload=payload)

    def propose(report, original):
        original["nodes"][0]["postconditions"] = [
            {"operator": "exists", "reference": "artifact://copy/result"}
        ]
        return original

    result = RepairLoop(studio, verifier).run(
        p.project_id, build["build_id"], cases, policy, propose
    )
    assert result["status"] == "passed"
    assert result["repair_rounds"] == 1
    assert result["build_id"] != build["build_id"]
    assert Compiler(studio).get(p.project_id, build["build_id"])["status"] == "rejected"
    assert Compiler(studio).get(p.project_id, result["build_id"])["status"] == "verified"
    studio.close()
