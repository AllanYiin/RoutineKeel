import sqlite3

import pytest
from test_governance import verified_approval
from test_verifier import verification_case
from workflow_studio.compiler.generator import Compiler
from workflow_studio.contracts import StudioError
from workflow_studio.governance.approvals import Approvals
from workflow_studio.governance.release import Releases
from workflow_studio.governance.reviews import ReviewStore
from workflow_studio.storage.migrations import APPLICATION_ID, MIGRATIONS, SCHEMA_VERSION
from workflow_studio.storage.sqlite import SQLiteRepository
from workflow_studio.ui.model import StudioUIModel
from workflow_studio.verifier.golden_cases import GoldenCases


def decide(studio, p, build, policy, action):
    reviews = studio.reviews(policy)
    return reviews.decide(
        p.project_id,
        studio.projects.get(p.project_id).version,
        build["build_id"],
        build["package_hash"],
        reviews.store.state(p.project_id, build["build_id"])["sequence"],
        action,
        actor="reviewer",
        reason="explicit fixture decision",
    )


def test_return_requires_fresh_report_and_blocks_rebuilding_same_revision(tmp_path):
    studio, p, build, cases, verifier, policy, approval = verified_approval(tmp_path)
    old = verifier.get(p.project_id, approval["report_id"])
    returned = decide(studio, p, build, policy, "return")
    assert returned["review"]["returned"]
    assert Approvals(studio).get(p.project_id, approval["approval_id"])["revoked_at"]
    assert verifier.get(p.project_id, old["report_id"]) == old
    with pytest.raises(StudioError):
        decide(studio, p, build, policy, "submit")
    sibling = studio.compile(p.project_id, build["revision_id"], policy)
    assert ReviewStore(studio.repository).state(p.project_id, sibling["build_id"])["returned"]
    with pytest.raises(StudioError) as exc:
        Approvals(studio).approve(
            p.project_id,
            build["build_id"],
            build["package_hash"],
            policy,
            actor="reviewer",
            reason="must resubmit",
        )
    assert exc.value.code == "REVIEW_RETURNED"
    fresh = verifier.verify(p.project_id, sibling["build_id"], cases, policy)
    assert fresh["status"] == "passed"
    assert fresh["review_sequence"] >= returned["review"]["returned_sequence"]
    assert not decide(studio, p, sibling, policy, "submit")["review"]["returned"]
    approved = Approvals(studio).approve(
        p.project_id,
        sibling["build_id"],
        sibling["package_hash"],
        policy,
        actor="reviewer",
        reason="reviewed fresh report",
    )
    assert (
        Releases(studio).publish(
            p.project_id, approved["approval_id"], tmp_path / "installed", policy
        )["state"]
        == "published"
    )
    studio.close()


def test_archive_restore_preserves_reports_and_prevents_new_runs(tmp_path):
    studio, p, build, cases, verifier, policy = verification_case(tmp_path)
    report = verifier.verify(p.project_id, build["build_id"], cases, policy)
    assert decide(studio, p, build, policy, "archive")["review"]["archived"]
    with pytest.raises(StudioError) as exc:
        verifier.verify(p.project_id, build["build_id"], cases, policy)
    assert exc.value.code == "BUILD_ARCHIVED"
    assert studio.reviews(policy).list(p.project_id) == []
    assert len(studio.reviews(policy).list(p.project_id, include_archived=True)) == 1
    assert Compiler(studio).get(p.project_id, build["build_id"])["status"] == "verified"
    assert verifier.get(p.project_id, report["report_id"]) == report
    assert not decide(studio, p, build, policy, "restore")["review"]["archived"]
    assert verifier.verify(p.project_id, build["build_id"], cases, policy)["status"] == "passed"
    studio.close()


def test_active_approval_and_published_build_are_protected_from_archive(tmp_path):
    studio, p, build, _, _, policy, approval = verified_approval(tmp_path)
    with pytest.raises(StudioError, match="撤回"):
        decide(studio, p, build, policy, "archive")
    release = Releases(studio).publish(
        p.project_id, approval["approval_id"], tmp_path / "installed", policy
    )
    manifest = tmp_path / "installed" / release["workflow_id"] / "flow.json"
    before = manifest.read_bytes()
    Approvals(studio).revoke(
        p.project_id, approval["approval_id"], actor="reviewer", reason="withdraw"
    )
    with pytest.raises(StudioError, match="發布"):
        decide(studio, p, build, policy, "archive")
    assert manifest.read_bytes() == before
    studio.close()


def test_review_cas_and_failure_keep_approval_and_project(tmp_path, monkeypatch):
    studio, p, build, _, _, policy, approval = verified_approval(tmp_path)
    manager = studio.reviews(policy)
    before = studio.projects.get(p.project_id)
    with pytest.raises(StudioError) as exc:
        manager.decide(
            p.project_id,
            before.version,
            build["build_id"],
            build["package_hash"],
            0,
            "return",
            actor="reviewer",
            reason="stale",
        )
    assert exc.value.code == "REVISION_CONFLICT"

    def fail(*args, **kwargs):
        raise StudioError("STORAGE_INVALID", "injected review write failure")

    monkeypatch.setattr(manager.store, "record", fail)
    with pytest.raises(StudioError):
        manager.decide(
            p.project_id,
            before.version,
            build["build_id"],
            build["package_hash"],
            manager.store.state(p.project_id, build["build_id"])["sequence"],
            "return",
            actor="reviewer",
            reason="must roll back",
        )
    assert studio.projects.get(p.project_id) == before
    assert Approvals(studio).get(p.project_id, approval["approval_id"])["revoked_at"] is None
    studio.close()


def test_new_case_and_updated_case_source_invalidate_old_acceptance(tmp_path):
    studio, p, build, cases, verifier, policy, approval = verified_approval(tmp_path)
    catalog = GoldenCases(studio)
    first = catalog.get(p.project_id, cases[0]["case_id"])
    second = catalog.save(
        p.project_id,
        studio.projects.get(p.project_id).version,
        {**first["specification"], "name": "新增案例"},
    )
    with pytest.raises(StudioError, match="清單"):
        Releases(studio).publish(
            p.project_id, approval["approval_id"], tmp_path / "installed", policy
        )
    bindings = cases + [{"case_id": second["case_id"], "version": 1, "output": "result"}]
    report = verifier.verify(p.project_id, build["build_id"], bindings, policy)
    assert report["status"] == "passed"
    ref = first["specification"]["input"]
    studio.sources.describe(
        p.project_id, ref["source_id"], studio.projects.get(p.project_id).version, "更新來源說明"
    )
    with pytest.raises(StudioError, match="案例來源"):
        Approvals(studio).validate_cases(p.project_id, report)
    studio.close()
    model = StudioUIModel(tmp_path / "studio")
    model.select_project(p.project_id)
    new = model.update_case(
        first["case_id"],
        1,
        model.project().version,
        name="更新來源版本",
        comparison="exact_bytes",
        use_current_input=True,
    )
    assert new["specification"]["input"]["version"] == 2
    assert model.case_assets(first["case_id"], 1)["input"]["ref"]["version"] == 1


def test_explicit_case_output_mapping_cannot_be_rebound(tmp_path):
    studio, p, build, cases, verifier, policy = verification_case(tmp_path)
    catalog = GoldenCases(studio)
    old = catalog.get(p.project_id, cases[0]["case_id"])
    updated = catalog.save(
        p.project_id,
        studio.projects.get(p.project_id).version,
        {**old["specification"], "output_name": "another"},
        case_id=old["case_id"],
    )
    cases[0]["version"] = updated["version"]
    report = verifier.verify(p.project_id, build["build_id"], cases, policy)
    assert report["status"] == "failed" and "交付物" in report["message"]
    studio.close()


def test_historical_flag_cannot_bypass_current_project_gate(tmp_path):
    studio, p, build, _, _, policy, approval = verified_approval(tmp_path)
    with pytest.raises(StudioError, match="歷史回復"):
        Releases(studio).publish(
            p.project_id, approval["approval_id"], tmp_path / "installed", policy, historical=True
        )
    studio.close()


def test_archived_project_cannot_start_design_operations(tmp_path):
    studio, p, build, cases, verifier, policy = verification_case(tmp_path)
    report = verifier.verify(p.project_id, build["build_id"], cases, policy)
    studio.projects.archive(p.project_id, studio.projects.get(p.project_id).version)
    for action in (
        lambda: studio.compile(p.project_id, build["revision_id"], policy),
        lambda: verifier.verify(p.project_id, build["build_id"], cases, policy),
        lambda: Approvals(studio).approve(
            p.project_id,
            build["build_id"],
            build["package_hash"],
            policy,
            actor="reviewer",
            reason="inactive",
        ),
    ):
        with pytest.raises(StudioError) as exc:
            action()
        assert exc.value.code == "PROJECT_INACTIVE"
    assert verifier.get(p.project_id, report["report_id"]) == report
    studio.close()


def test_build_preview_and_review_scope_are_project_bound(tmp_path):
    studio, p, build, _, _, policy = verification_case(tmp_path)
    other = studio.projects.create("other")
    with pytest.raises(StudioError):
        studio.reviews(policy).details(other.project_id, build["build_id"])
    studio.close()
    model = StudioUIModel(tmp_path / "studio")
    model.select_project(p.project_id)
    assert model.build_file(build["build_id"], "workflow.ir.json")
    with pytest.raises(StudioError):
        model.build_file(build["build_id"], "../../private.txt")
    model.select_project(other.project_id)
    with pytest.raises(StudioError):
        model.build_file(build["build_id"], "README.md")


def test_v012_upgrade_and_restore_keeps_legacy_state(tmp_path):
    root = tmp_path / "old"
    root.mkdir()
    with sqlite3.connect(root / "studio.sqlite3") as db:
        for version in range(1, 13):
            for statement in MIGRATIONS[version]:
                db.execute(statement)
        db.execute(f"PRAGMA application_id={APPLICATION_ID}")
        db.execute("PRAGMA user_version=12")
        db.execute(
            "INSERT INTO studio_projects VALUES ('p','kept','{}','active',1,NULL,'before','before')"
        )
    repo = SQLiteRepository(root)
    assert repo.schema_version == SCHEMA_VERSION
    snapshot = next((root / "backups").glob("before-v012-*.sqlite3"))
    repo.close()
    restored = SQLiteRepository.restore(snapshot, tmp_path / "restored")
    with sqlite3.connect(restored) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 12
        assert db.execute("SELECT name FROM studio_projects").fetchone()[0] == "kept"


def test_return_during_identical_manifest_publish_does_not_advance_pointer(tmp_path, monkeypatch):
    import workflow_studio.governance.release as release_module

    studio, p, first, cases, verifier, policy, approval = verified_approval(tmp_path)
    root = tmp_path / "installed"
    original = Releases(studio).publish(p.project_id, approval["approval_id"], root, policy)
    second = studio.compile(p.project_id, first["revision_id"], policy)
    assert (first["path"] / "package/flow.json").read_bytes() == (
        second["path"] / "package/flow.json"
    ).read_bytes()
    assert verifier.verify(p.project_id, second["build_id"], cases, policy)["status"] == "passed"
    fresh = Approvals(studio).approve(
        p.project_id,
        second["build_id"],
        second["package_hash"],
        policy,
        actor="reviewer",
        reason="same manifest distinct review",
    )
    original_write = release_module.atomic_bytes
    injected = []

    def interrupt_review(path, content):
        original_write(path, content)
        if path.suffix == ".whl" and not injected:
            injected.append(True)
            decide(studio, p, second, policy, "return")

    monkeypatch.setattr(release_module, "atomic_bytes", interrupt_review)
    with pytest.raises(StudioError) as exc:
        Releases(studio).publish(
            p.project_id,
            fresh["approval_id"],
            root,
            policy,
            expected_current=original["release_id"],
        )
    assert exc.value.code == "REVIEW_RETURNED"
    assert (
        Releases(studio).current(root, original["workflow_id"])["release_id"]
        == original["release_id"]
    )
    assert (
        studio.repository.connection.execute(
            "SELECT status FROM studio_releases WHERE build_id=?", (second["build_id"],)
        ).fetchone()[0]
        == "prepared"
    )
    studio.close()


def test_workbench_can_disable_current_release_with_reason(tmp_path):
    studio, p, build, _, _, policy, approval = verified_approval(tmp_path)
    root = tmp_path / "installed"
    release = Releases(studio).publish(p.project_id, approval["approval_id"], root, policy)
    folder = root / release["workflow_id"]
    (folder / "outputs").mkdir()
    (folder / "outputs/keep.txt").write_text("keep")
    studio.close()
    model = StudioUIModel(tmp_path / "studio")
    model.select_project(p.project_id)
    disabled = model.disable_release(release["release_id"], "operator", "manual stop")
    assert disabled["state"] == "disabled"
    assert disabled["actor"] == "operator" and disabled["reason"] == "manual stop"
    assert not (folder / "flow.json").exists()
    assert (folder / "outputs/keep.txt").read_text() == "keep"


def test_review_history_is_immutable_and_case_sources_can_be_previewed_before_update(tmp_path):
    studio, p, build, cases, verifier, policy = verification_case(tmp_path)
    assert verifier.verify(p.project_id, build["build_id"], cases, policy)["status"] == "passed"
    decide(studio, p, build, policy, "submit")
    for statement in (
        "UPDATE studio_build_reviews SET reason='changed'",
        "DELETE FROM studio_build_reviews",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            studio.repository.connection.execute(statement)
    case = GoldenCases(studio).get(p.project_id, cases[0]["case_id"])
    source = case["specification"]["input"]
    studio.sources.import_text(
        p.project_id,
        studio.projects.get(p.project_id).version,
        "new input",
        name=source["path"],
        source_id=source["source_id"],
    )
    studio.close()
    model = StudioUIModel(tmp_path / "studio")
    model.select_project(p.project_id)
    assert model.case_assets(case["case_id"], 1)["input"]["content"] == b"sample"
    assert (
        model.case_assets(case["case_id"], 1, current=["input"])["input"]["content"] == b"new input"
    )
    assert model.case_history(case["case_id"])[0] == case


def test_repeated_approval_is_idempotent_but_fresh_report_updates_release_reference(tmp_path):
    studio, p, build, cases, verifier, policy, approval = verified_approval(tmp_path)
    duplicate = Approvals(studio).approve(
        p.project_id,
        build["build_id"],
        build["package_hash"],
        policy,
        actor=approval["actor"],
        reason=approval["reason"],
    )
    assert duplicate == approval
    root = tmp_path / "installed"
    first = Releases(studio).publish(p.project_id, approval["approval_id"], root, policy)
    new_report = verifier.verify(p.project_id, build["build_id"], cases, policy)
    updated = Approvals(studio).approve(
        p.project_id,
        build["build_id"],
        build["package_hash"],
        policy,
        actor=approval["actor"],
        reason=approval["reason"],
    )
    assert updated["approval_id"] != approval["approval_id"]
    assert updated["report_id"] == new_report["report_id"]
    second = Releases(studio).publish(
        p.project_id, updated["approval_id"], root, policy, expected_current=first["release_id"]
    )
    assert second["approval_id"] == updated["approval_id"]
    assert (
        Releases(studio).current(root, first["workflow_id"])["release_id"] == second["release_id"]
    )
    assert Releases(studio).publish(p.project_id, updated["approval_id"], root, policy) == second
    studio.close()


def test_build_archive_is_independent_of_filesystem_timestamps(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    from compiler_helpers import build_ir

    studio, p, revision, policy = build_ir(tmp_path)
    original_write = Path.write_text
    timestamp = [1]

    def write_with_time(path, *args, **kwargs):
        result = original_write(path, *args, **kwargs)
        os.utime(path, (timestamp[0], timestamp[0]))
        return result

    monkeypatch.setattr(Path, "write_text", write_with_time)
    first = studio.compile(p.project_id, revision.revision_id, policy)
    timestamp[0] = 2_524_608_000
    second = studio.compile(p.project_id, revision.revision_id, policy)
    assert first["package_hash"] == second["package_hash"]
    studio.close()
