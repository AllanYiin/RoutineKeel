import copy
import hashlib
import json

import pytest
from compiler_helpers import build_ir, execute_package
from ir_cases import valid_ir

pytest.importorskip("PIL", reason="Runtime package fixtures require Studio source dependencies")


def test_generated_copy_executes_and_caches_via_workflow_service(tmp_path):
    studio, p, revision, policy = build_ir(tmp_path)
    build = studio.compile(p.project_id, revision.revision_id, policy)
    report = execute_package(build["path"] / "package", tmp_path / "runtime", b"first golden case")
    assert {"step.started", "step.finished"} <= set(report["_lifecycle"])
    assert (
        tmp_path / "runtime/output/artifacts/result/artifacts/result.txt"
    ).read_bytes() == b"first golden case"
    second = execute_package(build["path"] / "package", tmp_path / "runtime", b"first golden case")
    assert report["run_id"] != second["run_id"]
    assert all(step["status"] == "cached" for step in second["steps"])
    third = execute_package(
        build["path"] / "package", tmp_path / "runtime", b"different input", "different-output"
    )
    assert all(step["status"] == "executed" for step in third["steps"])
    assert (
        tmp_path / "runtime/different-output/artifacts/result/artifacts/result.txt"
    ).read_bytes() == b"different input"
    studio.close()


def test_generated_multistep_dag_matches_second_golden_case(tmp_path):
    payload = valid_ir()
    first = payload["nodes"][0]
    first.update(tool="text.normalize", parameters={"strip": True})
    first["inputs"]["source"]["port"]["format"] = "txt"
    first["outputs"]["result"]["format"] = "txt"
    payload["inputs"]["source"]["format"] = "txt"
    second = copy.deepcopy(first)
    second.update(id="report", tool="file.report", parameters={}, depends_on=["copy"])
    second["inputs"]["source"] = {"source": "artifact://copy/result", "port": {"type": "file"}}
    second["outputs"]["result"] = {"type": "file", "format": "json", "path": "report.json"}
    second["postconditions"][0]["reference"] = "artifact://report/result"
    payload["nodes"] = [second, first]
    payload["outputs"] = {"summary": "artifact://report/result"}
    studio, p, revision, policy = build_ir(tmp_path, payload)
    build = studio.compile(p.project_id, revision.revision_id, policy)
    result = execute_package(build["path"] / "package", tmp_path / "runtime", b" a \r\n b ")
    normalized = b"a\nb\n"
    actual = json.loads((tmp_path / "runtime/output/artifacts/summary/report.json").read_text())
    assert actual == {"bytes": len(normalized), "sha256": hashlib.sha256(normalized).hexdigest()}
    assert [s["id"] for s in result["steps"]] == [build["mapping"]["input_step"], "copy", "report"]
    studio.close()
