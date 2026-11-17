import json

import pytest
from compiler_helpers import build_ir, execute_package
from ir_cases import valid_ir
from workflow_studio.contracts import StudioError

pytest.importorskip("PIL", reason="Branch fixtures require Studio source dependencies")


def branch_ir():
    ir = valid_ir()
    ir["inputs"]["source"]["format"] = "txt"
    node = ir["nodes"][0]
    node.update(
        kind="composite_branch",
        tool="control.branch",
        parameters={
            "condition": {"operator": "file_size_gt", "value": 5},
            "when_true": {
                "tool": "text.normalize",
                "version": "1.0.0",
                "parameters": {"strip": True},
            },
            "when_false": {
                "tool": "text.normalize",
                "version": "1.0.0",
                "parameters": {"strip": False},
            },
        },
    )
    node["inputs"]["source"]["port"]["format"] = "txt"
    return ir


@pytest.mark.parametrize(
    "content,expected,choice",
    [(b"  long  ", b"long\n", "when_true"), (b" a ", b" a \n", "when_false")],
)
def test_both_branch_paths_execute_and_record_decisions(tmp_path, content, expected, choice):
    studio, p, revision, policy = build_ir(tmp_path, branch_ir())
    build = studio.compile(p.project_id, revision.revision_id, policy)
    execute_package(build["path"] / "package", tmp_path / "runtime", content)
    output = tmp_path / "runtime/output/artifacts/result"
    assert (output / "artifacts/result.txt").read_bytes() == expected
    assert json.loads((output / "branch.json").read_text())["selected"] == choice
    studio.close()


@pytest.mark.parametrize("problem", ["operator", "different_outputs", "nested", "parameter"])
def test_unsupported_or_unsafe_branch_rejected(tmp_path, problem):
    payload = branch_ir()
    params = payload["nodes"][0]["parameters"]
    if problem == "operator":
        params["condition"]["operator"] = "eval"
    elif problem == "different_outputs":
        params["when_false"] = {"tool": "file.report", "version": "1.0.0", "parameters": {}}
    elif problem == "nested":
        params["when_false"]["tool"] = "control.branch"
    else:
        params["when_false"]["parameters"] = {"shell": "command"}
    studio, p, revision, policy = build_ir(tmp_path, payload)
    with pytest.raises(StudioError):
        studio.compile(p.project_id, revision.revision_id, policy)
    studio.close()
