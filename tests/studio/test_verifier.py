import json

import pytest
from compiler_helpers import build_ir, runtime_python
from workflow_studio.compiler.generator import Compiler
from workflow_studio.verifier.golden_cases import GoldenCases
from workflow_studio.verifier.report import Verifier

pytest.importorskip("PIL", reason="Verifier fixtures require Studio source dependencies")


def verification_case(tmp_path, expected="sample", payload=None):
    studio, p, revision, policy = build_ir(tmp_path, payload)
    build = studio.compile(p.project_id, revision.revision_id, policy)
    source = studio.sources.import_text(p.project_id, 3, "sample", name="input.txt")
    expected_source = studio.sources.import_text(p.project_id, 4, expected, name="expected.txt")

    def ref(s):
        record = s.manifest["files"][0]
        return {
            "source_id": s.source_id,
            "version": s.version,
            "path": record["path"],
            "sha256": record["sha256"],
        }

    case = GoldenCases(studio).save(
        p.project_id,
        5,
        {
            "name": "固定成功案例",
            "input": ref(source),
            "expected": ref(expected_source),
            "comparison": "exact_bytes",
        },
    )
    bindings = [{"case_id": case["case_id"], "version": case["version"], "output": "result"}]
    verifier = Verifier(studio, runtime_python())
    return studio, p, build, bindings, verifier, policy


def test_independent_verifier_replays_records_and_repeats_safely(tmp_path):
    studio, p, build, cases, verifier, policy = verification_case(tmp_path)
    report = verifier.verify(p.project_id, build["build_id"], cases, policy)
    assert report["status"] == "passed", report
    assert report["cases"][0]["actual_sha256"] == report["cases"][0]["expected_sha256"]
    assert Compiler(studio).get(p.project_id, build["build_id"])["status"] == "verified"
    again = verifier.verify(p.project_id, build["build_id"], cases, policy)
    assert again["status"] == "passed"
    assert again["cases"][0]["run_id"] != report["cases"][0]["run_id"]
    assert verifier.get(p.project_id, report["report_id"]) == report
    studio.close()


def test_wrong_result_cannot_become_verified(tmp_path):
    studio, p, build, cases, verifier, policy = verification_case(tmp_path, expected="different")
    report = verifier.verify(p.project_id, build["build_id"], cases, policy)
    assert report["status"] == "failed"
    assert report["static"] == "passed"
    assert report["cases"][0]["difference"]
    assert Compiler(studio).get(p.project_id, build["build_id"])["status"] == "rejected"
    studio.close()


def test_tampered_code_is_rejected_without_execution(tmp_path, monkeypatch):
    studio, p, build, cases, verifier, policy = verification_case(tmp_path)
    package = build["path"] / "package"
    manifest = json.loads((package / "flow.json").read_text(encoding="utf-8"))
    wheel = package / manifest["package"]
    wheel.write_bytes(wheel.read_bytes() + b"tampered")

    def forbidden(*args, **kwargs):
        raise AssertionError("No dynamic execution is allowed after static failure")

    monkeypatch.setattr("workflow_studio.verifier.report.replay", forbidden)
    report = verifier.verify(p.project_id, build["build_id"], cases, policy)
    assert report["status"] == "failed"
    assert report["cases"] == []
    studio.close()


def test_runtime_failure_keeps_acceptance_and_prior_build(tmp_path):
    from ir_cases import valid_ir

    payload = valid_ir()
    payload["nodes"][0]["postconditions"] = [
        {"operator": "sha256", "reference": "artifact://copy/result", "value": "0" * 64}
    ]
    studio, p, build, cases, verifier, policy = verification_case(tmp_path, payload=payload)
    before = GoldenCases(studio).get(p.project_id, cases[0]["case_id"])
    report = verifier.verify(p.project_id, build["build_id"], cases, policy)
    assert report["status"] == "failed"
    assert report["error_code"] == "TEST_FAILED"
    assert GoldenCases(studio).get(p.project_id, cases[0]["case_id"]) == before
    studio.close()


def test_self_signed_untrusted_template_still_never_executes(tmp_path, monkeypatch):
    import zipfile

    from workflow_studio.compiler.package import digest, write_wheel

    studio, p, build, cases, verifier, policy = verification_case(tmp_path)
    package = build["path"] / "package"
    manifest = json.loads((package / "flow.json").read_text(encoding="utf-8"))
    wheel = package / manifest["package"]
    prefix = manifest["module"] + "/"
    with zipfile.ZipFile(wheel) as archive:
        compiled = archive.read(prefix + "compiled.json")
    candidate = build["path"] / "resealed.whl"
    write_wheel(
        candidate,
        manifest["module"],
        manifest["version"],
        {
            prefix + "__init__.py": b"",
            prefix + "adapter.py": b"raise RuntimeError('untrusted code')\n",
            prefix + "compiled.json": compiled,
        },
    )
    wheel.write_bytes(candidate.read_bytes())
    manifest["sha256"] = digest(wheel.read_bytes())
    (package / "flow.json").write_text(json.dumps(manifest), encoding="utf-8")
    bundle = next(build["path"].glob("*.zip"))
    with zipfile.ZipFile(bundle, "w") as archive:
        for file in package.iterdir():
            archive.write(file, prefix + file.name)
    studio.repository.connection.execute(
        "UPDATE studio_builds SET package_hash=? WHERE build_id=?",
        (digest(bundle.read_bytes()), build["build_id"]),
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Untrusted code must never execute")

    monkeypatch.setattr("workflow_studio.verifier.report.replay", forbidden)
    report = verifier.verify(p.project_id, build["build_id"], cases, policy)
    assert report["status"] == "failed"
    assert "受控模板" in report["message"]
    studio.close()
