import json

import pytest
from compiler_helpers import build_ir, execute_package, runtime_python
from generated_cases import csv_parameters, json_parameters
from ir_cases import valid_ir
from workflow_studio.compiler.handlers import verify_examples
from workflow_studio.compiler.runtime_program import run_program
from workflow_studio.contracts import StudioError
from workflow_studio.verifier.golden_cases import GoldenCases
from workflow_studio.verifier.report import Verifier

pytest.importorskip("PIL", reason="Package fixtures require Studio dependencies")


def program_ir(parameters, output_format):
    ir = valid_ir()
    ir["goal"] = {
        "description": parameters["purpose"],
        "success_criteria": ["符合固定成功案例的資料規則"],
    }
    node = ir["nodes"][0]
    node["tool"] = "data.transform_" + ("text" if output_format == "txt" else output_format)
    node["parameters"] = parameters
    node["outputs"]["result"] = {
        "type": "file",
        "format": output_format,
        "path": "artifacts/result." + output_format,
    }
    return ir


@pytest.mark.parametrize(
    "parameters,format", [(csv_parameters(), "csv"), (json_parameters(), "json")]
)
def test_generated_examples(parameters, format):
    result = verify_examples(parameters, format)
    assert len(result["tests"]) == 2
    assert result["evidence_kind"] == "model_proposed_examples_not_business_acceptance"


def test_csv_handler_is_packaged_executed_and_independently_verified(tmp_path):
    params = csv_parameters()
    studio, p, revision, policy = build_ir(tmp_path, program_ir(params, "csv"))
    build = studio.compile(p.project_id, revision.revision_id, policy)
    assert (build["path"] / "package/handler_copy.py").read_bytes() == params["program"].encode()
    assert (build["path"] / "package/generated-handler-tests.json").is_file()
    source_text = "id,amount,status\nX,300,open\nY,500,closed\nZ,20,open\n"
    expected_text = "id,amount\nX,300\n"
    report = execute_package(build["path"] / "package", tmp_path / "runtime", source_text.encode())
    assert report["token_usage"]["registered_requests"] == 0
    assert (
        tmp_path / "runtime/output/artifacts/result/artifacts/result.csv"
    ).read_bytes() == expected_text.encode()
    source = studio.sources.import_text(p.project_id, 3, source_text, name="input.csv")
    expected = studio.sources.import_text(p.project_id, 4, expected_text, name="expected.csv")

    def ref(item):
        record = item.manifest["files"][0]
        return {
            "source_id": item.source_id,
            "version": item.version,
            "path": record["path"],
            "sha256": record["sha256"],
        }

    case = GoldenCases(studio).save(
        p.project_id,
        5,
        {
            "name": "獨立訂單案例",
            "input": ref(source),
            "expected": ref(expected),
            "comparison": "exact_bytes",
        },
    )
    result = Verifier(studio, runtime_python()).verify(
        p.project_id,
        build["build_id"],
        [{"case_id": case["case_id"], "version": 1, "output": "result"}],
        policy,
    )
    assert result["status"] == "passed", result
    studio.close()


def test_json_aggregation_is_not_a_fixed_copy_or_format_operation(tmp_path):
    studio, p, revision, policy = build_ir(tmp_path, program_ir(json_parameters(), "json"))
    build = studio.compile(p.project_id, revision.revision_id, policy)
    data = '[{"category":"A","quantity":10},{"category":"B","quantity":2},{"category":"A","quantity":7}]'
    execute_package(build["path"] / "package", tmp_path / "runtime", data.encode())
    assert json.loads(
        (tmp_path / "runtime/output/artifacts/result/artifacts/result.json").read_text()
    ) == {"A": 17, "B": 2}
    studio.close()


def test_failed_examples_block_packaging(tmp_path):
    params = csv_parameters()
    params["tests"][0]["expected"] = "id,amount\nwrong,0\n"
    studio, p, revision, policy = build_ir(tmp_path, program_ir(params, "csv"))
    with pytest.raises(StudioError) as caught:
        studio.compile(p.project_id, revision.revision_id, policy)
    assert caught.value.code == "TEST_FAILED"
    assert not (studio.repository.root / "projects").exists()
    studio.close()


def test_examples_cannot_authorize_reflection(tmp_path):
    params = csv_parameters()
    params["program"] = "def transform(text, params):\n    return text.__class__.__name__"
    studio, p, revision, policy = build_ir(tmp_path, program_ir(params, "csv"))
    with pytest.raises(StudioError):
        studio.compile(p.project_id, revision.revision_id, policy)
    studio.close()


def test_enumeration_and_comprehensions():
    code = "def transform(text, params):\n    rows = text.splitlines()\n    return json_dumps({str(i): row.strip().upper() for i, row in enumerate(rows, start=1) if row.strip()})"
    assert json.loads(run_program(code, " a \n\nb\n")) == {"1": "A", "3": "B"}


def test_argument_contract_and_workbench_edit_without_code_changes(tmp_path):
    from workflow_studio.conversation.session import StudioSession

    params = csv_parameters()
    studio, p, _revision, _policy = build_ir(tmp_path, program_ir(params, "csv"))
    studio.close()
    model = StudioSession(tmp_path / "studio")
    model.select_project(p.project_id)
    with pytest.raises(StudioError):
        model.edit_node("copy", arguments={"minimum": "invalid"})
    changed = model.edit_node("copy", arguments={"minimum": 200})
    assert changed.ir["revision"] == 2
    assert changed.ir["nodes"][0]["parameters"]["program"] == params["program"]
    assert changed.ir["nodes"][0]["parameters"]["arguments"] == {"minimum": 200}
