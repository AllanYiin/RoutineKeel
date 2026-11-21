import copy
import hashlib
import json

import pytest
from compiler_helpers import build_ir, runtime_python
from ir_cases import valid_ir
from workflow_studio.contracts import StudioError
from workflow_studio.conversation.graph import GraphDraft
from workflow_studio.conversation.session import StudioSession
from workflow_studio.llm.usage import UsageLedger


def report_node(studio, project_id, policy):
    return studio.editor(policy).template(
        project_id,
        "report",
        "file.report@1.0.0",
        {"source": "artifact://copy/result"},
        {"result": "artifacts/report.json"},
    )


def test_manual_add_connect_and_deliver_replays_on_runtime_without_llm(tmp_path):
    payload = valid_ir()
    payload["inputs"]["source"]["format"] = "txt"
    node = payload["nodes"][0]
    node["tool"] = "text.normalize"
    node["inputs"]["source"]["port"]["format"] = "txt"
    node["outputs"]["result"]["format"] = "txt"
    node["parameters"] = {"strip": True, "final_newline": True}
    studio, p, original, policy = build_ir(tmp_path, payload)
    added = report_node(studio, p.project_id, policy)
    revision = studio.editor(policy).apply(
        p.project_id, 3, upsert_nodes=[added], outputs={"result": "artifact://report/result"}
    )
    assert revision.ir["revision"] == 2
    assert studio.projects.get_revision(p.project_id, original.revision_id) == original
    assert UsageLedger(studio.repository).summary(p.project_id)["calls"] == 0
    studio.close()
    model = StudioSession(tmp_path / "studio", runtime_python=runtime_python())
    model.select_project(p.project_id)
    source, expected = tmp_path / "input.txt", tmp_path / "expected.json"
    source.write_bytes(b"  alpha  \r\n beta\r\n")
    normalized = b"alpha\nbeta\n"
    expected.write_bytes(
        (
            json.dumps(
                {"bytes": len(normalized), "sha256": hashlib.sha256(normalized).hexdigest()},
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    )
    model.add_case(source, expected, "result")
    report = model.verify()
    assert report["status"] == "passed", json.dumps(report, ensure_ascii=False)
    assert report["runtime_model_authorized"] is False


def test_atomic_rewire_and_delete_has_no_dangling_intermediate_version(tmp_path):
    studio, p, original, policy = build_ir(tmp_path)
    editor = studio.editor(policy)
    added = report_node(studio, p.project_id, policy)
    second = editor.apply(
        p.project_id, 3, upsert_nodes=[added], outputs={"result": "artifact://report/result"}
    )
    before = studio.projects.get(p.project_id)
    with pytest.raises(StudioError):
        editor.apply(p.project_id, before.version, delete_nodes=["copy"])
    assert studio.projects.get(p.project_id) == before
    draft = GraphDraft(second.ir)
    draft.reconnect("report", "input://source", [], "artifacts/report.json")
    draft.ir["nodes"] = [draft.node("report")]
    third = editor.apply(p.project_id, before.version, **draft.changes())
    assert [n["id"] for n in third.ir["nodes"]] == ["report"]
    assert third.ir["nodes"][0]["depends_on"] == []
    assert len(studio.projects.revisions(p.project_id)) == 3
    assert studio.projects.get_revision(p.project_id, second.revision_id) == second
    studio.close()


@pytest.mark.parametrize("fault", ["cycle", "missing", "type", "path", "output", "duplicate"])
def test_invalid_whole_graph_rolls_back_without_new_revision(tmp_path, fault):
    studio, p, original, policy = build_ir(tmp_path)
    added = report_node(studio, p.project_id, policy)
    copied = copy.deepcopy(original.ir["nodes"][0])
    updates = [copied, added]
    outputs = {"result": "artifact://report/result"}
    if fault == "cycle":
        copied["depends_on"] = ["report"]
    elif fault == "missing":
        added["inputs"]["source"]["source"] = "artifact://missing/result"
    elif fault == "type":
        added["inputs"]["source"]["port"]["type"] = "string"
    elif fault == "path":
        added["outputs"]["result"]["path"] = "../outside.json"
    elif fault == "output":
        outputs = {"result": "artifact://copy/result"}  # Nonterminal is not a deliverable.
    else:
        updates.append(added)
    with pytest.raises(StudioError):
        studio.editor(policy).apply(p.project_id, 3, upsert_nodes=updates, outputs=outputs)
    assert studio.projects.get(p.project_id).version == 3
    assert studio.projects.revisions(p.project_id) == [original]
    studio.close()


def test_input_type_declaration_and_existing_contracts_are_checked_together(tmp_path):
    studio, p, original, policy = build_ir(tmp_path)
    editor = studio.editor(policy)
    node = editor.template(
        p.project_id,
        "text",
        "text.normalize@1.0.0",
        {"source": "input://source"},
        {"result": "clean.txt"},
    )
    with pytest.raises(StudioError, match="型別"):
        editor.apply(
            p.project_id,
            3,
            upsert_nodes=[node],
            delete_nodes=["copy"],
            outputs={"result": "artifact://text/result"},
        )
    inputs = copy.deepcopy(original.ir["inputs"])
    inputs["source"]["format"] = "txt"
    changed = editor.apply(
        p.project_id,
        3,
        upsert_nodes=[node],
        delete_nodes=["copy"],
        inputs=inputs,
        outputs={"result": "artifact://text/result"},
    )
    assert changed.ir["inputs"]["source"]["format"] == "txt"
    studio.close()


def test_editor_uses_current_policy_and_rejects_stale_revision(tmp_path):
    studio, p, original, policy = build_ir(tmp_path)
    node = report_node(studio, p.project_id, policy)
    tool = studio.tools.get("file.report", "1.0.0")
    studio.tools.set_status("file.report", "1.0.0", tool["record_version"], "disabled")
    with pytest.raises(StudioError):
        studio.editor(policy).apply(
            p.project_id, 3, upsert_nodes=[node], outputs={"result": "artifact://report/result"}
        )
    studio.projects.update(p.project_id, 3, name="其他編輯")
    with pytest.raises(StudioError) as exc:
        studio.editor(policy).apply(p.project_id, 3, upsert_nodes=[])
    assert exc.value.code == "REVISION_CONFLICT"
    assert studio.projects.get_revision(p.project_id, original.revision_id) == original
    studio.close()


def test_duplicate_custom_handler_preserves_algorithm_and_rewrites_self_checks(tmp_path):
    from generated_cases import csv_parameters
    from test_generated_handlers import program_ir

    studio, p, original, policy = build_ir(tmp_path, program_ir(csv_parameters(), "csv"))
    draft = GraphDraft(original.ir)
    node = draft.duplicate("copy")
    assert node["parameters"] == original.ir["nodes"][0]["parameters"]
    assert node["postconditions"][0]["reference"] == f"artifact://{node['id']}/result"
    draft.ir["outputs"]["second"] = f"artifact://{node['id']}/result"
    changed = studio.editor(policy).apply(p.project_id, 3, **draft.changes())
    assert len(changed.ir["nodes"]) == 2
    assert len(changed.ir["outputs"]) == 2
    assert studio.compile(p.project_id, changed.revision_id, policy)
    studio.close()


def test_local_draft_never_reuses_removed_id_or_mutates_source_snapshot():
    ir = valid_ir()
    ir["nodes"][0]["id"] = "step_1"
    draft = GraphDraft(ir)
    draft.ir["nodes"].clear()
    assert draft.next_id() == "step_2"
    assert len(ir["nodes"]) == 1


def test_simple_node_edit_after_discard_uses_next_global_revision_and_invalidates_cache(tmp_path):
    studio, p, original, policy = build_ir(tmp_path)
    second = studio.history.reuse(p.project_id, original.revision_id, 3, policy)
    studio.history.discard(p.project_id, second.revision_id, 4)
    studio.close()
    model = StudioSession(tmp_path / "studio")
    model.select_project(p.project_id)
    model.build_id, model.approval_id = "cached-build", "cached-approval"
    changed = model.edit_node("copy", output_name="new.txt")
    assert changed.ir["revision"] == 3
    assert model.build_id is model.approval_id is None
