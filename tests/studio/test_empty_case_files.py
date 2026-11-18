import pytest
from compiler_helpers import build_ir, runtime_python
from test_acceptance_corpus import CATALOG, ROOT
from test_generated_handlers import program_ir
from workflow_studio.contracts import StudioError
from workflow_studio.ingestion.models import ImportLimits
from workflow_studio.ingestion.preview import preview_file
from workflow_studio.ui.model import StudioUIModel
from workflow_studio.verifier.golden_cases import GoldenCases
from workflow_studio.verifier.report import Verifier


def test_empty_expected_file_can_be_added_as_an_exact_business_case(tmp_path):
    model = StudioUIModel(tmp_path)
    p = model.create_project(
        "empty result", "extract matching lines", "no matches produces zero bytes"
    )
    pair = next(c for c in CATALOG["cases"] if c["id"] == "C10")["held_out"][-1]
    case = model.add_case(ROOT / pair["input"], ROOT / pair["expected"])
    with model.service() as studio:
        actual_input, expected = GoldenCases(studio).bytes(p.project_id, case["case_id"])
        assert actual_input and expected == b""


def test_blank_pasted_description_and_invalid_structured_files_still_rejected(tmp_path):
    model = StudioUIModel(tmp_path)
    p = model.create_project("invalid", "goal", "success")
    with model.service() as studio:
        with pytest.raises(StudioError):
            studio.sources.import_text(p.project_id, p.version, "   ")
        for suffix, content in (("json", b""), ("mmd", b""), ("txt", b"\x00")):
            path = tmp_path / ("empty." + suffix)
            path.write_bytes(content)
            with pytest.raises(StudioError):
                studio.sources.import_path(p.project_id, p.version, path)
        assert studio.sources.list(p.project_id) == []
    with pytest.raises(StudioError):
        preview_file("bad.txt", b"\x00", ImportLimits())


def test_zero_byte_result_is_verified_by_actual_runtime(tmp_path):
    parameters = {
        "purpose": "extract error lines including no-match output",
        "program": "def transform(text, params):\n    return ''.join([line[6:] + '\\n' for line in text.splitlines() if line.startswith('ERROR|')])\n",
        "arguments": {},
        "argument_types": {},
        "tests": [
            {"input": "ERROR|one\n", "expected": "one\n", "arguments": {}},
            {"input": "INFO|ok\n", "expected": "", "arguments": {}},
        ],
    }
    studio, p, revision, policy = build_ir(tmp_path, program_ir(parameters, "txt"))
    try:
        pair = next(c for c in CATALOG["cases"] if c["id"] == "C10")["held_out"][-1]
        refs = {}
        for side in ("input", "expected"):
            source = studio.sources.import_path(
                p.project_id, studio.projects.get(p.project_id).version, ROOT / pair[side]
            )
            record = source.manifest["files"][0]
            refs[side] = {
                "source_id": source.source_id,
                "version": source.version,
                "path": record["path"],
                "sha256": record["sha256"],
            }
        case = GoldenCases(studio).save(
            p.project_id,
            studio.projects.get(p.project_id).version,
            {"name": "empty exact output", **refs, "comparison": "exact_bytes"},
        )
        build = studio.compile(p.project_id, revision.revision_id, policy)
        report = Verifier(studio, runtime_python()).verify(
            p.project_id,
            build["build_id"],
            [{"case_id": case["case_id"], "version": 1, "output": "result"}],
            policy,
        )
        assert report["status"] == "passed", report
    finally:
        studio.close()
