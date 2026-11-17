import copy
import sqlite3

import pytest
from compiler_helpers import build_ir
from test_verifier import verification_case
from workflow_studio.contracts import StudioError
from workflow_studio.governance.evidence import EvidenceRecords
from workflow_studio.storage.migrations import APPLICATION_ID, MIGRATIONS
from workflow_studio.storage.sqlite import SQLiteRepository
from workflow_studio.verifier.static import verify_static


def confirm(studio, p, policy):
    return studio.evidence(policy).decide(
        p.project_id,
        studio.projects.get(p.project_id).version,
        "description",
        actor="業務使用者",
        reason="已對照來源與固定成功案例",
    )


def test_human_confirmation_survives_compile_replay_edit_and_reopen(tmp_path):
    studio, p, _, cases, verifier, policy = verification_case(tmp_path)
    revision = confirm(studio, p, policy)
    evidence = revision.ir["evidence"][0]
    assert evidence["confidence_label"] == "human_confirmed"
    assert studio.confirmed_evidence(p.project_id, revision.ir) == {evidence["verified_by"]}
    build = studio.compile(p.project_id, revision.revision_id, policy)
    report = verifier.verify(p.project_id, build["build_id"], cases, policy)
    assert report["status"] == "passed", report
    updated = studio.editor(policy).apply(p.project_id, studio.projects.get(p.project_id).version)
    assert updated.ir["evidence"] == revision.ir["evidence"]
    assert (
        EvidenceRecords(studio.repository).get(p.project_id, evidence["verified_by"])["actor"]
        == "業務使用者"
    )
    studio.close()
    from workflow_studio.service import StudioService

    with StudioService.open_workspace(tmp_path / "studio") as reopened:
        assert reopened.confirmed_evidence(p.project_id, updated.ir) == {evidence["verified_by"]}


@pytest.mark.parametrize(
    "change", ["claim", "locator", "source_version", "evidence_id", "machine_verified"]
)
def test_confirmation_cannot_be_reused_for_changed_rule(tmp_path, change):
    studio, p, original, policy = build_ir(tmp_path)
    revision = confirm(studio, p, policy)
    payload = copy.deepcopy(revision.ir)
    payload["revision"] += 1
    item = payload["evidence"][0]
    if change == "claim":
        item["claim"] = "可隨意刪除資料"
    elif change == "locator":
        item["evidence_locator"] += ":changed"
    elif change == "source_version":
        item["source_version"] += 1
    elif change == "evidence_id":
        item["id"] = "other"
        payload["nodes"][0]["evidence_refs"] = ["other"]
    else:
        item["confidence_label"] = "verified"
    before = studio.projects.get(p.project_id)
    with pytest.raises(StudioError):
        studio.save_ir(p.project_id, before.version, payload, policy)
    assert studio.projects.get(p.project_id) == before
    studio.close()


def test_cross_project_forgery_and_source_instructions_cannot_confirm(tmp_path):
    studio, p, original, policy = build_ir(tmp_path)
    confirmed = confirm(studio, p, policy)
    other = studio.projects.create("另一專案", p.goal)
    item = confirmed.ir["evidence"][0]
    with pytest.raises(StudioError):
        EvidenceRecords(studio.repository).confirmed_ids(other.project_id, [item])
    with pytest.raises(StudioError) as exc:
        studio.evidence(policy).edit(p.project_id, studio.projects.get(p.project_id).version, item)
    assert exc.value.code == "APPROVAL_REQUIRED"
    modified = copy.deepcopy(confirmed.ir)
    modified["revision"] += 1
    modified["evidence"][0]["verified_by"] = "source-says-approved"
    with pytest.raises(StudioError):
        studio.save_ir(p.project_id, studio.projects.get(p.project_id).version, modified, policy)
    studio.close()


def test_withdrawal_blocks_old_build_validation_but_retains_report(tmp_path):
    studio, p, _, cases, verifier, policy = verification_case(tmp_path)
    revision = confirm(studio, p, policy)
    build = studio.compile(p.project_id, revision.revision_id, policy)
    report = verifier.verify(p.project_id, build["build_id"], cases, policy)
    assert report["status"] == "passed"
    withdrawn = studio.evidence(policy).decide(
        p.project_id,
        studio.projects.get(p.project_id).version,
        "description",
        actor="業務使用者",
        reason="找到需要重新確認的例外",
        revoke=True,
    )
    assert withdrawn.ir["evidence"][0]["confidence_label"] == "inferred"
    assert studio.confirmed_evidence(p.project_id, revision.ir) == set()
    with pytest.raises(StudioError):
        verify_static(studio, build, policy)
    assert verifier.get(p.project_id, report["report_id"]) == report
    again = confirm(studio, p, policy)
    assert again.ir["evidence"][0]["verified_by"] != revision.ir["evidence"][0]["verified_by"]
    assert studio.confirmed_evidence(p.project_id, again.ir)
    studio.close()


def test_rule_edit_binding_and_removal_keep_history_and_atomic_failure(tmp_path):
    studio, p, original, policy = build_ir(tmp_path)
    rules = studio.evidence(policy)
    item = copy.deepcopy(original.ir["evidence"][0])
    item.update(id="second", claim="補充規則", confidence_label="inferred", verified_by=None)
    before = studio.projects.get(p.project_id)
    with pytest.raises(StudioError):
        rules.edit(p.project_id, before.version, item, node_ids=["missing"])
    assert studio.projects.get(p.project_id) == before
    changed = rules.edit(p.project_id, before.version, item, node_ids=["copy"])
    assert changed.ir["nodes"][0]["evidence_refs"] == ["description", "second"]
    with pytest.raises(StudioError, match="引用"):
        rules.remove(p.project_id, studio.projects.get(p.project_id).version, "second")
    changed = rules.edit(p.project_id, studio.projects.get(p.project_id).version, item, node_ids=[])
    removed = rules.remove(p.project_id, studio.projects.get(p.project_id).version, "second")
    assert len(removed.ir["evidence"]) == 1
    assert (
        studio.projects.get_revision(p.project_id, changed.revision_id).ir["evidence"][1]["claim"]
        == "補充規則"
    )
    studio.close()


def test_confirmation_failure_rolls_back_decision_and_revision(tmp_path, monkeypatch):
    studio, p, original, policy = build_ir(tmp_path)
    before = studio.projects.get(p.project_id)

    def fail(*args, **kwargs):
        raise StudioError("STORAGE_INVALID", "injected save failure")

    monkeypatch.setattr(studio, "save_ir", fail)
    with pytest.raises(StudioError):
        confirm(studio, p, policy)
    assert EvidenceRecords(studio.repository).list(p.project_id) == []
    assert studio.projects.get(p.project_id) == before
    studio.close()


def test_migrate_v009_and_restore_verified_backup(tmp_path):
    root = tmp_path / "old"
    root.mkdir()
    db = sqlite3.connect(root / "studio.sqlite3")
    for version in range(1, 10):
        for statement in MIGRATIONS[version]:
            db.execute(statement)
    db.execute(f"PRAGMA application_id={APPLICATION_ID}")
    db.execute("PRAGMA user_version=9")
    db.execute(
        "INSERT INTO studio_projects VALUES ('p','kept','{}','active',1,NULL,'before','before')"
    )
    db.commit()
    db.close()
    repo = SQLiteRepository(root)
    assert repo.schema_version == 10
    assert repo.connection.execute("SELECT name FROM studio_projects").fetchone()[0] == "kept"
    backup = next((root / "backups").glob("before-v009-*.sqlite3"))
    repo.close()
    restored = SQLiteRepository.restore(backup, tmp_path / "restored")
    with sqlite3.connect(restored) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9
        assert db.execute("SELECT name FROM studio_projects").fetchone()[0] == "kept"
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='studio_evidence_decisions'"
        ).fetchone()


def test_decisions_are_immutable_and_stale_confirmation_does_not_overwrite(tmp_path):
    studio, p, original, policy = build_ir(tmp_path)
    confirm(studio, p, policy)
    for statement in (
        "UPDATE studio_evidence_decisions SET actor='changed'",
        "DELETE FROM studio_evidence_decisions",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            studio.repository.connection.execute(statement)
    with pytest.raises(StudioError) as exc:
        studio.evidence(policy).decide(
            p.project_id, 3, "description", actor="stale", reason="not current"
        )
    assert exc.value.code == "REVISION_CONFLICT"
    studio.close()


def test_model_repair_preserves_independent_human_confirmation(tmp_path):
    from test_model_repair import setup

    studio, p, _, cases, repair, _ = setup(tmp_path)
    confirmed = confirm(studio, p, repair.policy)
    build = studio.compile(p.project_id, confirmed.revision_id, repair.policy)
    report = repair.verifier.verify(p.project_id, build["build_id"], cases, repair.policy)
    assert report["status"] == "failed"
    prepared = repair.prepare(p.project_id, report["report_id"], cases)
    events = list(repair.run(prepared, authorized_hash=prepared.request_hash))
    assert events[-1]["status"] == "passed"
    latest = studio.projects.get_revision(
        p.project_id, studio.projects.get(p.project_id).current_revision_id
    )
    assert latest.ir["evidence"] == confirmed.ir["evidence"]
    studio.close()


def test_revoked_during_publish_preparation_preserves_previous_manifest(tmp_path, monkeypatch):
    import workflow_studio.governance.release as release_module
    from test_governance import verified_approval
    from workflow_studio.governance.approvals import Approvals
    from workflow_studio.governance.release import Releases

    studio, p, old_build, cases, verifier, policy, approval = verified_approval(tmp_path)
    root = tmp_path / "installed"
    released = Releases(studio).publish(p.project_id, approval["approval_id"], root, policy)
    manifest = root / released["workflow_id"] / "flow.json"
    previous = manifest.read_bytes()
    confirmed = confirm(studio, p, policy)
    build = studio.compile(p.project_id, confirmed.revision_id, policy)
    assert verifier.verify(p.project_id, build["build_id"], cases, policy)["status"] == "passed"
    approved = Approvals(studio).approve(
        p.project_id,
        build["build_id"],
        build["package_hash"],
        policy,
        actor="fixture",
        reason="confirmed rule",
    )
    write = release_module.atomic_bytes
    injected = []

    def withdraw_before_switch(path, content):
        write(path, content)
        if path.suffix == ".whl" and not injected:
            injected.append(True)
            studio.evidence(policy).decide(
                p.project_id,
                studio.projects.get(p.project_id).version,
                "description",
                actor="fixture",
                reason="withdraw during prepare",
                revoke=True,
            )

    monkeypatch.setattr(release_module, "atomic_bytes", withdraw_before_switch)
    with pytest.raises(StudioError):
        Releases(studio).publish(
            p.project_id,
            approved["approval_id"],
            root,
            policy,
            expected_current=released["release_id"],
        )
    assert injected and manifest.read_bytes() == previous
    assert (
        Releases(studio).current(root, released["workflow_id"])["build_id"] == old_build["build_id"]
    )
    studio.close()


def test_case_assets_use_immutable_image_version_without_desktop(tmp_path):
    from PIL import Image
    from workflow_studio.ui.model import StudioUIModel

    model = StudioUIModel(tmp_path / "studio")
    model.create_project("圖片案例", "複製圖片", "位元組相符")
    first, second = tmp_path / "first.png", tmp_path / "second.png"
    Image.new("RGB", (120, 60), "red").save(first)
    Image.new("RGB", (30, 90), "blue").save(second)
    case = model.add_case(first, first)
    original = model.case_assets(case["case_id"], 1)
    model.update_case(
        case["case_id"],
        1,
        model.project().version,
        name="新版圖片",
        comparison="exact_bytes",
        input_path=second,
        expected_path=second,
    )
    assert model.case_assets(case["case_id"], 1) == original
    assert original["input"]["preview"]["width"] == 120
    assert model.case_assets(case["case_id"], 2)["expected"]["content"] == second.read_bytes()


def test_withdrawal_invalidates_all_earlier_confirmations_for_same_content_after_vacuum(tmp_path):
    studio, p, original, policy = build_ir(tmp_path)
    first = confirm(studio, p, policy)
    item = copy.deepcopy(first.ir["evidence"][0])
    item.update(confidence_label="inferred", verified_by=None)
    studio.evidence(policy).edit(p.project_id, studio.projects.get(p.project_id).version, item)
    second = confirm(studio, p, policy)
    studio.evidence(policy).decide(
        p.project_id,
        studio.projects.get(p.project_id).version,
        "description",
        actor="fixture",
        reason="撤回相同內容的先前判斷",
        revoke=True,
    )
    studio.repository.connection.execute("VACUUM")
    assert not studio.confirmed_evidence(p.project_id, first.ir)
    assert not studio.confirmed_evidence(p.project_id, second.ir)
    latest = confirm(studio, p, policy)
    assert studio.confirmed_evidence(p.project_id, latest.ir)
    studio.close()
