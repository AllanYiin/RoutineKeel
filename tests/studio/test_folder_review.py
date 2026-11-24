import copy
import sqlite3

import pytest
from compiler_helpers import runtime_python
from test_folder_discovery import folder_studio
from workflow_studio.contracts import StudioError
from workflow_studio.conversation.session import StudioSession
from workflow_studio.discovery.evidence_graph import EvidenceGraphs
from workflow_studio.discovery.folder import FolderDiscovery
from workflow_studio.governance.evidence import EvidenceRecords
from workflow_studio.registry.policy import ToolPolicy


def review_model(tmp_path, entries=None):
    studio, project, source, folder = folder_studio(
        tmp_path, entries or {"input/a.txt": "sample\n", "output/a.txt": "sample\n"}
    )
    graph = FolderDiscovery(studio).analyze(project.project_id, source.source_id, 2)
    studio.close()
    model = StudioSession(tmp_path / "workspace", runtime_python=runtime_python())
    model.select_project(project.project_id)
    return model, graph, source, folder


def test_confirmed_relation_adoption_has_auditable_basis_and_real_replay(tmp_path):
    model, snapshot, _, _ = review_model(tmp_path)
    graph_id = snapshot["graph_id"]
    edge = snapshot["graph"]["relations"][0]
    updated = model.decide_folder_relation(
        graph_id,
        model.project().version,
        edge["relation_id"],
        "confirm",
        "reviewer",
        "verified the intended copy step",
    )
    assert updated["version"] == 2
    candidates = model.candidates_from_graph(graph_id)
    selected = next(i for i, c in enumerate(candidates) if c["relations"] == [edge["relation_id"]])
    assert candidates[selected]["ir"]["evidence"][0]["confidence_label"] == "inferred"
    revision = model.accept_candidate(selected)
    evidence = revision.ir["evidence"][0]
    assert evidence["confidence_label"] == "human_confirmed"
    assert graph_id in evidence["claim"] and edge["tool"] in evidence["claim"]
    with model.service() as studio:
        record = EvidenceRecords(studio.repository).get(model.project_id, evidence["verified_by"])
        assert record["actor"] == "reviewer"
        assert record["reason"] == "verified the intended copy step"
        assert studio.confirmed_evidence(model.project_id, revision.ir) == {evidence["verified_by"]}
    assert model.verify()["status"] == "passed"
    reopened = StudioSession(tmp_path / "workspace", runtime_python=runtime_python())
    reopened.select_project(model.project_id)
    assert reopened.folder_graph(graph_id)["history"][0] == snapshot
    assert reopened.folder_asset(graph_id, 1, edge["from"])["content"] == b"sample\n"


@pytest.mark.parametrize("action", ["remove", "confirm", "unconfirm"])
def test_relation_changes_invalidate_previously_loaded_candidate(tmp_path, action):
    model, snapshot, _, _ = review_model(tmp_path)
    stale = model.candidates_from_graph(snapshot["graph_id"])[0]
    model.decide_folder_relation(
        snapshot["graph_id"],
        model.project().version,
        snapshot["graph"]["relations"][0]["relation_id"],
        action,
        "reviewer",
        "correct evidence",
    )
    assert not model.candidates
    model.candidates = [stale]
    with pytest.raises(StudioError) as exc:
        model.accept_candidate(0)
    assert exc.value.code == "REVISION_CONFLICT"
    assert model.revision() is None
    assert model.cases() == []


def test_role_changes_enable_unknown_files_and_reset_confirmations(tmp_path):
    model, snapshot, _, _ = review_model(tmp_path, {"a.txt": "sample\n", "b.txt": "sample\n"})
    assert not model.candidates_from_graph(snapshot["graph_id"])
    roles = model.revise_folder_roles(
        snapshot["graph_id"],
        model.project().version,
        {"a.txt": "input", "b.txt": "output"},
        "reviewer",
        "identify the input and final output",
    )
    assert roles["graph"]["relations"]
    edge = roles["graph"]["relations"][0]
    model.decide_folder_relation(
        snapshot["graph_id"],
        model.project().version,
        edge["relation_id"],
        "confirm",
        "reviewer",
        "copy matches intended work",
    )
    revised = model.revise_folder_roles(
        snapshot["graph_id"],
        model.project().version,
        {"b.txt": "intermediate"},
        "reviewer",
        "this is an intermediate file",
    )
    assert all(r["confidence_label"] == "inferred" for r in revised["graph"]["relations"])
    assert not model.candidates_from_graph(snapshot["graph_id"])
    assert len(model.folder_graph(snapshot["graph_id"])["history"]) == 4
    assert revised["graph"]["decisions"][-1]["reason"] == "this is an intermediate file"


def test_replacement_and_pending_status_preserve_exact_history(tmp_path):
    model, snapshot, _, _ = review_model(tmp_path)
    first, second = snapshot["graph"]["relations"][:2]
    replaced = model.decide_folder_relation(
        snapshot["graph_id"],
        model.project().version,
        first["relation_id"],
        "replace",
        "reviewer",
        "prefer this transform",
        alternative_id=second["relation_id"],
    )
    chosen = next(
        r for r in replaced["graph"]["relations"] if r["relation_id"] == first["relation_id"]
    )
    assert chosen["tool"] == second["tool"]
    pending = model.decide_folder_relation(
        snapshot["graph_id"],
        model.project().version,
        first["relation_id"],
        "unconfirm",
        "reviewer",
        "needs further evidence",
    )
    edge = next(
        r for r in pending["graph"]["relations"] if r["relation_id"] == first["relation_id"]
    )
    assert edge["confidence_label"] == "inferred" and "verified_by" not in edge
    assert model.folder_graph(snapshot["graph_id"])["history"][1] == replaced


def test_stale_decision_and_invalid_roles_leave_no_partial_graph(tmp_path):
    model, snapshot, _, _ = review_model(tmp_path)
    version = model.project().version
    model.decide_folder_relation(
        snapshot["graph_id"],
        version,
        snapshot["graph"]["relations"][0]["relation_id"],
        "confirm",
        "reviewer",
        "confirmed",
    )
    with pytest.raises(StudioError) as exc:
        model.decide_folder_relation(
            snapshot["graph_id"],
            version,
            snapshot["graph"]["relations"][0]["relation_id"],
            "remove",
            "reviewer",
            "stale view",
        )
    assert exc.value.code == "REVISION_CONFLICT"
    with pytest.raises(StudioError):
        model.revise_folder_roles(
            snapshot["graph_id"],
            model.project().version,
            {"missing.txt": "input"},
            "reviewer",
            "invalid path",
        )
    assert len(model.folder_graph(snapshot["graph_id"])["history"]) == 2


def test_source_replacement_keeps_preview_but_rejects_decision_and_adoption(tmp_path):
    model, snapshot, source, folder = review_model(tmp_path)
    candidate = model.candidates_from_graph(snapshot["graph_id"])[0]
    (folder / "input/a.txt").write_text("changed", encoding="utf-8")
    with model.service() as studio:
        studio.sources.import_path(
            model.project_id, model.project().version, folder, source_id=source.source_id
        )
    assert not model.folder_graph(snapshot["graph_id"])["source_current"]
    assert model.folder_asset(snapshot["graph_id"], 1, "input/a.txt")["content"] == b"sample\n"
    with pytest.raises(StudioError):
        model.decide_folder_relation(
            snapshot["graph_id"],
            model.project().version,
            snapshot["graph"]["relations"][0]["relation_id"],
            "confirm",
            "reviewer",
            "stale source",
        )
    model.candidates = [candidate]
    with pytest.raises(StudioError):
        model.accept_candidate(0)
    assert model.revision() is None


def test_archived_graph_cannot_be_silently_restored_by_decision(tmp_path):
    model, snapshot, _, _ = review_model(tmp_path)
    with model.service() as studio:
        EvidenceGraphs(studio.repository).archive(
            model.project_id, snapshot["graph_id"], model.project().version
        )
    with pytest.raises(StudioError):
        model.decide_folder_relation(
            snapshot["graph_id"],
            model.project().version,
            snapshot["graph"]["relations"][0]["relation_id"],
            "confirm",
            "reviewer",
            "must reject",
        )
    assert len(model.folder_graph(snapshot["graph_id"])["history"]) == 2


def test_candidate_tampering_and_failed_case_commit_do_not_leave_confirmation(tmp_path):
    model, snapshot, _, _ = review_model(tmp_path)
    model.decide_folder_relation(
        snapshot["graph_id"],
        model.project().version,
        snapshot["graph"]["relations"][0]["relation_id"],
        "confirm",
        "reviewer",
        "copy intended",
    )
    candidate = copy.deepcopy(model.candidates_from_graph(snapshot["graph_id"])[0])
    model.candidates[0]["ir"]["nodes"][0]["timeout_seconds"] += 1
    with pytest.raises(StudioError):
        model.accept_candidate(0)
    with model.service() as studio:
        studio.repository.connection.execute(
            "CREATE TRIGGER fail_case BEFORE INSERT ON studio_test_cases BEGIN SELECT RAISE(ABORT, 'injected'); END"
        )
        version = model.project().version
        with pytest.raises(sqlite3.IntegrityError):
            FolderDiscovery(studio).accept(
                model.project_id, version, candidate, ToolPolicy("windows")
            )
        assert studio.projects.get(model.project_id).version == version
        assert not EvidenceRecords(studio.repository).list(model.project_id)
    assert model.revision() is None and not model.cases()


def test_removing_last_relation_marks_output_as_unknown(tmp_path):
    model, snapshot, _, _ = review_model(tmp_path)
    for edge in snapshot["graph"]["relations"]:
        final = model.decide_folder_relation(
            snapshot["graph_id"],
            model.project().version,
            edge["relation_id"],
            "remove",
            "reviewer",
            "not the actual processing step",
        )
    assert any(item["path"] == "output/a.txt" for item in final["graph"]["unknowns"])
    assert not model.candidates_from_graph(snapshot["graph_id"])
    assert len(final["graph"]["decisions"]) == len(snapshot["graph"]["relations"])


def test_legacy_confirmation_without_reason_is_not_promoted_to_ir_confirmation(tmp_path):
    model, snapshot, _, _ = review_model(tmp_path)
    with model.service() as studio:
        EvidenceGraphs(studio.repository).revise_relation(
            model.project_id,
            snapshot["graph_id"],
            model.project().version,
            snapshot["graph"]["relations"][0]["relation_id"],
            decision="confirm",
            actor="legacy reviewer",
        )
    model.candidates_from_graph(snapshot["graph_id"])
    revision = model.accept_candidate(0)
    assert revision.ir["evidence"][0]["confidence_label"] == "inferred"
    with model.service() as studio:
        assert not EvidenceRecords(studio.repository).list(model.project_id)


def test_discarded_candidate_does_not_reuse_ir_ordinal(tmp_path):
    model, snapshot, _, _ = review_model(tmp_path)
    model.candidates_from_graph(snapshot["graph_id"])
    first = model.accept_candidate(0)
    with model.service() as studio:
        studio.projects.discard_revision(
            model.project_id, first.revision_id, model.project().version
        )
    model.candidates_from_graph(snapshot["graph_id"])
    second = model.accept_candidate(0)
    assert second.ir["revision"] == first.ir["revision"] + 1
    assert second.ir["workflow_id"] == first.ir["workflow_id"]
    with model.service() as studio:
        assert studio.projects.get_revision(model.project_id, first.revision_id).ir == first.ir
