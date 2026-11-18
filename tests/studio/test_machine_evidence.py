import copy
import json
import sqlite3

import pytest
from test_verifier import verification_case
from workflow_studio.contracts import StudioError
from workflow_studio.governance.machine_evidence import MachineEvidence
from workflow_studio.verifier.replay import output_bytes


def verified_case(tmp_path):
    studio, project, build, cases, verifier, policy = verification_case(tmp_path)
    report = verifier.verify(project.project_id, build["build_id"], cases, policy)
    assert report["status"] == "passed"
    machine = MachineEvidence(studio.repository)
    return studio, project, build, cases, verifier, policy, report, machine


def test_attach_replay_evidence_is_verifiable_and_new_build_still_needs_test(tmp_path):
    studio, p, build, cases, verifier, policy, report, machine = verified_case(tmp_path)
    revision = machine.attach(
        studio, p.project_id, studio.projects.get(p.project_id).version, report["report_id"], policy
    )
    evidence = revision.ir["evidence"][-1]
    assert evidence["confidence_label"] == "verified"
    assert "僅證明這次執行" in evidence["claim"]
    assert studio.confirmed_evidence(p.project_id, revision.ir) == {evidence["verified_by"]}
    assert (
        machine.inspect(p.project_id, evidence["verified_by"], revision.ir)["case"]["run_id"]
        == report["cases"][0]["run_id"]
    )
    new = studio.compile(p.project_id, revision.revision_id, policy)
    assert new["status"] == "candidate" and new["package_hash"] != build["package_hash"]
    assert verifier.verify(p.project_id, new["build_id"], cases, policy)["status"] == "passed"
    studio.close()


@pytest.mark.parametrize("change", ["claim", "source", "label", "execution"])
def test_untrusted_payload_cannot_borrow_verification_for_changed_claim(tmp_path, change):
    studio, p, _, _, _, policy, report, machine = verified_case(tmp_path)
    revision = machine.attach(
        studio, p.project_id, studio.projects.get(p.project_id).version, report["report_id"], policy
    )
    payload = copy.deepcopy(revision.ir)
    item = payload["evidence"][-1]
    if change == "claim":
        item["claim"] = "All future inputs are correct"
    elif change == "source":
        item["evidence_locator"] = "other.txt"
    elif change == "label":
        item["confidence_label"] = "human_confirmed"
    else:
        payload["nodes"][0]["timeout_seconds"] += 1
    payload["revision"] += 1
    with pytest.raises(StudioError):
        studio.save_ir(p.project_id, studio.projects.get(p.project_id).version, payload, policy)
    assert studio.projects.get(p.project_id).current_revision_id == revision.revision_id
    studio.close()


def test_graph_edit_downgrades_old_machine_evidence_without_mutating_history(tmp_path):
    studio, p, _, _, _, policy, report, machine = verified_case(tmp_path)
    revision = machine.attach(
        studio, p.project_id, studio.projects.get(p.project_id).version, report["report_id"], policy
    )
    node = copy.deepcopy(revision.ir["nodes"][0])
    node["timeout_seconds"] += 1
    changed = studio.editor(policy).apply(
        p.project_id, studio.projects.get(p.project_id).version, upsert_nodes=[node]
    )
    assert changed.ir["evidence"][-1]["confidence_label"] == "inferred"
    assert changed.ir["evidence"][-1]["verified_by"] is None
    assert studio.projects.get_revision(p.project_id, revision.revision_id).ir == revision.ir
    studio.close()


def test_actual_artifact_damage_invalidates_evidence_and_does_not_replay(tmp_path, monkeypatch):
    studio, p, build, _, _, policy, report, machine = verified_case(tmp_path)
    original = studio.projects.get_revision(p.project_id, build["revision_id"]).ir
    root = build["path"] / "verification" / report["report_id"] / "case-1"
    runtime = json.loads((root / "runtime-result.json").read_text())
    output = report["cases"][0]["output"]
    primary = root / "output" / runtime["named_outputs"][output]["primary"]
    node_id = original["outputs"][output].split("//")[1].split("/")[0]
    node = next(n for n in original["nodes"] if n["id"] == node_id)
    (primary.parent / node["outputs"]["result"]["path"]).write_bytes(b"damaged")
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: pytest.fail("must never replay"))
    with pytest.raises(StudioError):
        machine.attach(
            studio,
            p.project_id,
            studio.projects.get(p.project_id).version,
            report["report_id"],
            policy,
        )
    assert studio.projects.get(p.project_id).current_revision_id == build["revision_id"]
    studio.close()


def test_primary_metadata_path_cannot_escape_replay_workspace(tmp_path):
    with pytest.raises(StudioError):
        output_bytes(
            tmp_path,
            {"named_outputs": {"result": {"primary": "../outside.json"}}},
            "result",
            {
                "outputs": {"result": "artifact://copy/result"},
                "nodes": [{"id": "copy", "outputs": {"result": {"path": "artifacts/out.txt"}}}],
            },
        )


@pytest.mark.parametrize(
    "file",
    ["report.json", "case-1/runtime-result.json", "case-1/generated/flow.json", "case-1/input.bin"],
)
def test_modified_or_missing_proof_cannot_support_verified_label(tmp_path, file):
    studio, p, build, _, _, policy, report, machine = verified_case(tmp_path)
    path = build["path"] / "verification" / report["report_id"] / file
    path.write_bytes(b"changed proof")
    with pytest.raises(StudioError):
        machine.attach(
            studio,
            p.project_id,
            studio.projects.get(p.project_id).version,
            report["report_id"],
            policy,
        )
    assert studio.projects.get(p.project_id).current_revision_id == build["revision_id"]
    studio.close()


def test_failed_and_foreign_report_cannot_create_verified_evidence(tmp_path):
    studio, p, build, cases, verifier, policy = verification_case(tmp_path, expected="different")
    report = verifier.verify(p.project_id, build["build_id"], cases, policy)
    machine = MachineEvidence(studio.repository)
    with pytest.raises(StudioError):
        machine.attach(
            studio,
            p.project_id,
            studio.projects.get(p.project_id).version,
            report["report_id"],
            policy,
        )
    other = studio.projects.create("other")
    with pytest.raises(StudioError):
        machine.inspect(
            other.project_id,
            f"replay:{report['report_id']}:1",
            studio.projects.get_revision(p.project_id, build["revision_id"]).ir,
        )
    studio.close()


def test_evidence_only_edits_retain_machine_fact_and_cannot_human_confirm_it(tmp_path):
    studio, p, _, _, _, policy, report, machine = verified_case(tmp_path)
    revision = machine.attach(
        studio, p.project_id, studio.projects.get(p.project_id).version, report["report_id"], policy
    )
    rule = copy.deepcopy(revision.ir["evidence"][0])
    rule["claim"] = "More detailed business explanation"
    changed = studio.evidence(policy).edit(
        p.project_id, studio.projects.get(p.project_id).version, rule
    )
    assert changed.ir["evidence"][-1] == revision.ir["evidence"][-1]
    assert studio.confirmed_evidence(p.project_id, changed.ir)
    with pytest.raises(StudioError):
        studio.evidence(policy).decide(
            p.project_id,
            studio.projects.get(p.project_id).version,
            revision.ir["evidence"][-1]["id"],
            actor="reviewer",
            reason="cannot relabel",
        )
    studio.close()


def test_event_failure_and_stale_version_do_not_partially_attach_evidence(tmp_path):
    studio, p, build, _, _, policy, report, machine = verified_case(tmp_path)
    version = studio.projects.get(p.project_id).version
    with pytest.raises(StudioError):
        machine.attach(studio, p.project_id, version - 1, report["report_id"], policy)
    studio.repository.connection.execute(
        "CREATE TRIGGER fail_machine_event BEFORE INSERT ON studio_events BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        machine.attach(studio, p.project_id, version, report["report_id"], policy)
    assert studio.projects.get(p.project_id).version == version
    assert studio.projects.get(p.project_id).current_revision_id == build["revision_id"]
    studio.close()


def test_json_comparison_evidence_does_not_claim_equal_bytes(tmp_path):
    from workflow_studio.verifier.golden_cases import GoldenCases

    studio, p, build, cases, verifier, policy = verification_case(tmp_path, expected='{ "n": 1 }')
    source = studio.sources.import_text(
        p.project_id, studio.projects.get(p.project_id).version, '{"n":1}', name="json-input.txt"
    )
    catalog = GoldenCases(studio)
    case = catalog.get(p.project_id, cases[0]["case_id"])
    spec = case["specification"]
    record = source.manifest["files"][0]
    spec["input"] = {
        "source_id": source.source_id,
        "version": source.version,
        "path": record["path"],
        "sha256": record["sha256"],
    }
    spec["comparison"] = "json_equal"
    updated = catalog.save(
        p.project_id, studio.projects.get(p.project_id).version, spec, case_id=case["case_id"]
    )
    cases[0]["version"] = updated["version"]
    report = verifier.verify(p.project_id, build["build_id"], cases, policy)
    assert report["status"] == "passed"
    assert report["cases"][0]["actual_sha256"] != report["cases"][0]["expected_sha256"]
    machine = MachineEvidence(studio.repository)
    revision = machine.attach(
        studio, p.project_id, studio.projects.get(p.project_id).version, report["report_id"], policy
    )
    assert "JSON 內容相同" in revision.ir["evidence"][-1]["claim"]
    assert machine.valid_ids(p.project_id, revision.ir)
    studio.close()


def test_model_repair_carries_only_system_downgrade_across_rounds(tmp_path):
    from test_model_repair import RepairProvider
    from workflow_studio.discovery.planner import DiscoveryResult
    from workflow_studio.llm.usage import Budget
    from workflow_studio.verifier.model_repair import ModelRepair

    studio, p, _, _, verifier, policy, report, machine = verified_case(tmp_path)
    revision = machine.attach(
        studio, p.project_id, studio.projects.get(p.project_id).version, report["report_id"], policy
    )
    provider = RepairProvider()
    repair = ModelRepair(
        studio,
        provider,
        Budget(5, 1_000_000, 8000, 1_000_000, 1_000_000),
        policy,
        runtime_python=verifier.runtime_python,
    )
    current = revision.ir
    for _ in range(2):
        proposal = copy.deepcopy(current)
        proposal["nodes"][0]["timeout_seconds"] += 1
        current = repair._proposal(
            current,
            revision.ir,
            DiscoveryResult(analysis="adjust timeout", ir=proposal, questions=[]),
            p.project_id,
        )
        assert current["evidence"][-1]["confidence_label"] == "inferred"
        assert current["evidence"][-1]["verified_by"] is None
    bad = copy.deepcopy(current)
    bad["evidence"][-1]["claim"] = "fabricated conclusion"
    with pytest.raises(StudioError):
        repair._proposal(
            current,
            revision.ir,
            DiscoveryResult(analysis="bad", ir=bad, questions=[]),
            p.project_id,
        )
    assert provider.calls == 0
    studio.close()


def test_ui_model_attaches_and_reopens_machine_proof_without_granting_approval(tmp_path):
    from workflow_studio.ui.model import StudioUIModel

    studio, p, _, _, verifier, _, report, _ = verified_case(tmp_path)
    runtime = verifier.runtime_python
    studio.close()
    model = StudioUIModel(tmp_path / "studio", runtime_python=runtime)
    model.select_project(p.project_id)
    revision = model.attach_machine_evidence(model.project().version, report["report_id"])
    assert model.build_id is None and model.approval_id is None
    reopened = StudioUIModel(tmp_path / "studio", runtime_python=runtime)
    reopened.select_project(p.project_id)
    proof = reopened.machine_evidence_details(revision.ir["evidence"][-1]["verified_by"])
    assert proof["report_id"] == report["report_id"]
    assert reopened.revision().ir == revision.ir
