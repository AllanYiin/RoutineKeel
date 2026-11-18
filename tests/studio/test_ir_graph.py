import copy

import pytest
from ir_cases import tools, valid_ir
from workflow_studio.contracts import StudioError
from workflow_studio.ir.validator import validate_ir


def dag():
    payload = valid_ir()
    second = copy.deepcopy(payload["nodes"][0])
    second.update(id="second", depends_on=["copy"])
    second["inputs"]["source"]["source"] = "artifact://copy/result"
    second["postconditions"][0]["reference"] = "artifact://second/result"
    payload["nodes"].insert(0, second)  # Input order need not be topological.
    payload["outputs"]["result"] = "artifact://second/result"
    return payload


def test_valid_static_dag():
    assert len(validate_ir(dag(), tools()).nodes) == 2


@pytest.mark.parametrize(
    "problem",
    [
        "cycle",
        "missing_dependency",
        "undeclared_read",
        "missing_port",
        "duplicate",
        "missing_input",
        "missing_evidence",
    ],
)
def test_invalid_graph_rejected(problem):
    payload = dag()
    first, second = payload["nodes"][1], payload["nodes"][0]
    if problem == "cycle":
        first["depends_on"] = ["second"]
    elif problem == "missing_dependency":
        second["depends_on"] = ["unknown"]
    elif problem == "undeclared_read":
        second["depends_on"] = []
    elif problem == "missing_port":
        second["inputs"]["source"]["source"] = "artifact://copy/unknown"
    elif problem == "duplicate":
        second["id"] = "copy"
    elif problem == "missing_input":
        first["inputs"] = {}
    elif problem == "missing_evidence":
        first["evidence_refs"] = ["unknown"]
    with pytest.raises(StudioError):
        validate_ir(payload, tools())
