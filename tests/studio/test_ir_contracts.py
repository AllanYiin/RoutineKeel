import copy
import json
from pathlib import Path

import pytest
from ir_cases import tools, valid_ir
from workflow_studio.contracts import StudioError
from workflow_studio.ir.models import WorkflowIR
from workflow_studio.ir.validator import validate_ir


def test_valid_linear_ir_and_generated_schema_are_consistent():
    ir = validate_ir(valid_ir(), tools())
    assert ir.nodes[0].id == "copy"
    schema = (
        Path(__file__).resolve().parents[2]
        / "workflow_studio/src/workflow_studio/contracts/workflow_ir.schema.json"
    )
    assert json.loads(schema.read_text(encoding="utf-8")) == WorkflowIR.model_json_schema()


@pytest.mark.parametrize(
    "field,value", [("ir_version", "2.0"), ("revision", "1"), ("invented", True)]
)
def test_unknown_version_field_or_coercion_rejected(field, value):
    payload = valid_ir()
    payload[field] = value
    with pytest.raises(StudioError) as caught:
        validate_ir(payload, tools())
    assert caught.value.code == "IR_SCHEMA_INVALID"


@pytest.mark.parametrize(
    "path", ["../escape.txt", "C:/private.txt", "/tmp/escape", "a\\escape.txt", "CON.txt"]
)
def test_machine_paths_and_traversal_rejected(path):
    payload = valid_ir()
    payload["nodes"][0]["outputs"]["result"]["path"] = path
    with pytest.raises(StudioError):
        validate_ir(payload, tools())


def test_missing_tool_wrong_platform_and_type_rejected():
    payload = valid_ir()
    with pytest.raises(StudioError) as caught:
        validate_ir(payload, {})
    assert caught.value.code == "TOOL_NOT_ALLOWED"
    registry = tools()
    registry["file.copy@1.0.0"].platforms = ["windows"]
    with pytest.raises(StudioError) as caught:
        validate_ir(payload, registry)
    assert caught.value.code == "PLATFORM_UNSUPPORTED"
    payload["inputs"]["source"]["type"] = "integer"
    with pytest.raises(StudioError) as caught:
        validate_ir(payload, tools())
    assert caught.value.code == "CONTRACT_MISMATCH"


def test_evidence_cannot_self_attest_verified_status():
    payload = valid_ir()
    payload["evidence"][0].update(confidence_label="verified", verified_by="made-up")
    with pytest.raises(StudioError) as caught:
        validate_ir(payload, tools())
    assert caught.value.code == "EVIDENCE_INSUFFICIENT"


@pytest.mark.parametrize(
    "change",
    [
        {"postconditions": []},
        {"retry_policy": {"mode": "never", "max_attempts": 2}},
        {"cache_policy": {"mode": "content_addressed", "fingerprint_fields": ["inputs"]}},
        {"kind": "llm"},
        {"side_effects": "external"},
    ],
)
def test_incomplete_execution_policies_rejected(change):
    payload = valid_ir()
    payload["nodes"][0].update(copy.deepcopy(change))
    with pytest.raises(StudioError):
        validate_ir(payload, tools())
