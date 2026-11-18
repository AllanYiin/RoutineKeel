import pytest
from compiler_helpers import build_ir, runtime_python
from workflow_studio.contracts import StudioError
from workflow_studio.governance.approvals import Approvals
from workflow_studio.verifier.comparison import compare, read_json
from workflow_studio.verifier.golden_cases import GoldenCases
from workflow_studio.verifier.report import Verifier


@pytest.mark.parametrize(
    "expected,actual,equal",
    [
        ('{"b":2,"a":[1,true,null]}', '{ "a": [1.0,true,null], "b":2e0 }', True),
        ("[1,2]", "[2,1]", False),
        ("true", "1", False),
        ("false", "0", False),
        ('"1"', "1", False),
        ("{}", "[]", False),
        ('{"a":null}', "{}", False),
        ("[1]", "[1,2]", False),
        ("9007199254740993", "9007199254740992", False),
        ("1.00000000000000000000000001", "1.00000000000000000000000002", False),
        ("1e999", "1e999", True),
        ("-0", "0.0", True),
        ('"中文"', '"\\u4e2d\\u6587"', True),
    ],
)
def test_json_semantics_preserve_values_and_types(expected, actual, equal):
    assert compare("json_equal", expected.encode(), actual.encode())[0] is equal


@pytest.mark.parametrize(
    "invalid",
    [
        b'{"x":1,"x":2}',
        b"NaN",
        b"Infinity",
        b"-Infinity",
        b"null null",
        b"\xff",
        b'"\\ud800"',
        b'{"\\udfff":1}',
        b"[" * 66 + b"0" + b"]" * 66,
        b"[" + b"0," * 100_000 + b"0]",
    ],
)
def test_invalid_json_never_passes_even_if_bytes_match(invalid):
    with pytest.raises(StudioError):
        compare("json_equal", invalid, invalid)
    matched, detail = compare("json_equal", b"{}", invalid)
    assert not matched
    assert "JSON" in detail["reason"]


def test_explicit_rule_and_structural_difference():
    assert compare("exact_bytes", b'{"a":1}', b'{ "a":1 }')[0] is False
    matched, detail = compare("json_equal", b'{"a/b~":[true]}', b'{"a/b~":[1]}')
    assert not matched
    assert detail["json_pointer"] == "/a~1b~0/0"
    with pytest.raises(StudioError):
        compare("ignore_errors", b"{}", b"{}")
    with pytest.raises(ValueError, match="10 MiB"):
        read_json(b" " * (10 * 1024 * 1024 + 1))


def test_real_replay_rule_versions_and_approval_invalidation(tmp_path):
    studio, project, revision, policy = build_ir(tmp_path)
    pid = project.project_id
    try:
        build = studio.compile(pid, revision.revision_id, policy)

        def source(text):
            item = studio.sources.import_text(pid, studio.projects.get(pid).version, text)
            file = item.manifest["files"][0]
            return {
                "source_id": item.source_id,
                "version": item.version,
                "path": file["path"],
                "sha256": file["sha256"],
            }

        spec = {
            "name": "等值 JSON",
            "input": source('{"b":2,"a":1}'),
            "expected": source('{ "a": 1.0, "b": 2 }'),
            "comparison": "json_equal",
        }
        cases = GoldenCases(studio)
        first = cases.save(pid, studio.projects.get(pid).version, spec)
        bindings = [{"case_id": first["case_id"], "version": 1, "output": "result"}]
        verifier = Verifier(studio, runtime_python())
        report = verifier.verify(pid, build["build_id"], bindings, policy)
        assert report["status"] == "passed", report
        result = report["cases"][0]
        assert result["comparison"] == "json_equal"
        assert result["actual_sha256"] != result["expected_sha256"]
        Approvals(studio).approve(
            pid,
            build["build_id"],
            build["package_hash"],
            policy,
            actor="fixture",
            reason="explicit JSON rule",
        )
        spec["comparison"] = "exact_bytes"
        second = cases.save(pid, studio.projects.get(pid).version, spec, case_id=first["case_id"])
        assert second["version"] == 2
        assert cases.get(pid, first["case_id"], 1)["specification"]["comparison"] == "json_equal"
        with pytest.raises(StudioError, match="重新驗證"):
            Approvals(studio).validate_cases(pid, report)
        bindings[0]["version"] = 2
        failed = verifier.verify(pid, build["build_id"], bindings, policy)
        assert failed["status"] == "failed"
        assert failed["cases"][0]["comparison"] == "exact_bytes"
        assert failed["cases"][0]["acceptance_hash"] != result["acceptance_hash"]
        assert verifier.get(pid, report["report_id"]) == report
    finally:
        studio.close()
