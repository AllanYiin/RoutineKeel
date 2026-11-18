import copy

import pytest
from compiler_helpers import build_ir
from ir_cases import valid_ir
from workflow_studio.contracts import StudioError

pytest.importorskip(
    "PIL", reason="Compiler integration fixtures require Studio source dependencies"
)


@pytest.mark.parametrize("problem", ["parameters", "retry", "reserved_path", "nonterminal_output"])
def test_unsupported_semantics_rejected_before_package(tmp_path, problem):
    payload = valid_ir()
    node = payload["nodes"][0]
    if problem == "parameters":
        node["parameters"] = {"shell": "anything"}
    elif problem == "retry":
        node["retry_policy"] = {"mode": "transient_only", "max_attempts": 2}
    elif problem == "reserved_path":
        node["outputs"]["result"]["path"] = "result.json"
    elif problem == "nonterminal_output":
        next_node = copy.deepcopy(node)
        next_node.update(id="next", depends_on=["copy"])
        next_node["inputs"]["source"]["source"] = "artifact://copy/result"
        next_node["postconditions"][0]["reference"] = "artifact://next/result"
        payload["nodes"].append(next_node)
    studio, p, revision, policy = build_ir(tmp_path, payload)
    with pytest.raises(StudioError):
        studio.compile(p.project_id, revision.revision_id, policy)
    assert not (studio.repository.root / "projects").exists()
    studio.close()


def test_tool_disabled_after_ir_save_blocks_compilation(tmp_path):
    studio, p, revision, policy = build_ir(tmp_path)
    studio.tools.set_status("file.copy", "1.0.0", 1, "disabled")
    with pytest.raises(StudioError) as caught:
        studio.compile(p.project_id, revision.revision_id, policy)
    assert caught.value.code == "TOOL_NOT_ALLOWED"
    studio.close()
