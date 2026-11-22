import json
import shutil
import sqlite3

import pytest
from filelock import FileLock
from test_governance import verified_approval
from workflow_studio.contracts import StudioError
from workflow_studio.governance.approvals import Approvals
from workflow_studio.governance.recovery import ReleaseRecovery
from workflow_studio.governance.release import Releases
from workflow_studio.storage.migrations import APPLICATION_ID, MIGRATIONS, SCHEMA_VERSION
from workflow_studio.storage.sqlite import SQLiteRepository


def failed_publish(tmp_path, monkeypatch, stage="before_manifest", *, existing=False):
    import workflow_studio.governance.release as module

    studio, p, build, cases, verifier, policy, approval = verified_approval(tmp_path)
    root = tmp_path / "installed"
    previous = None
    if existing:
        previous = Releases(studio).publish(p.project_id, approval["approval_id"], root, policy)
        build = studio.compile(p.project_id, build["revision_id"], policy)
        assert verifier.verify(p.project_id, build["build_id"], cases, policy)["status"] == "passed"
        approval = Approvals(studio).approve(
            p.project_id,
            build["build_id"],
            build["package_hash"],
            policy,
            actor="reviewer",
            reason="new approval",
        )
    write = module.atomic_bytes

    def fail(path, data):
        if stage == "before_journal" and path.name == "journal.json":
            raise OSError("injected before journal")
        if path.parent.name in {"copy-example", "FLOW_copy-example"}:
            if stage == "wheel_partial" and path.suffix == ".whl":
                path.parent.mkdir(parents=True, exist_ok=True)
                path.with_name(path.name + "." + "a" * 32 + ".tmp").write_bytes(data[:20])
                raise OSError("injected partial wheel")
            if stage == "before_manifest" and path.name == "flow.json":
                raise OSError("injected before manifest")
        write(path, data)
        if (
            stage == "after_manifest"
            and path.parent.name in {"copy-example", "FLOW_copy-example"}
            and path.name == "flow.json"
        ):
            raise OSError("injected after manifest")
        if stage == "after_pointer" and path.name == "current.json":
            raise OSError("injected after pointer")

    with monkeypatch.context() as patch:
        patch.setattr(module, "atomic_bytes", fail)
        with pytest.raises(OSError):
            Releases(studio).publish(
                p.project_id,
                approval["approval_id"],
                root,
                policy,
                expected_current=previous["release_id"] if previous else None,
            )
    pending = [r for r in Releases(studio).history(p.project_id) if r["status"] == "prepared"][-1]
    return studio, p, build, cases, verifier, policy, approval, root, pending, previous


@pytest.mark.parametrize("stage", ["before_journal", "before_manifest", "wheel_partial"])
def test_unapplied_preparation_can_end_archive_and_republish_without_deletion(
    tmp_path, monkeypatch, stage
):
    studio, p, build, cases, verifier, policy, approval, root, pending, _ = failed_publish(
        tmp_path, monkeypatch, stage
    )
    recovery = ReleaseRecovery(studio)
    observed = recovery.inspect(p.project_id, pending["release_id"])
    assert observed["outcome"] == "not_applied"
    files = {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.name != "publish.lock"
    }
    result = recovery.reconcile(
        p.project_id,
        pending["release_id"],
        observed["observation_hash"],
        actor="operator",
        reason="end failed preparation",
    )
    assert result["status"] == "abandoned"
    assert Releases(studio).current(root, "copy-example") is None
    for relative, content in files.items():
        assert (root / relative).read_bytes() == content
    Approvals(studio).revoke(p.project_id, approval["approval_id"])
    reviews = studio.reviews(policy)
    archived = reviews.decide(
        p.project_id,
        studio.projects.get(p.project_id).version,
        build["build_id"],
        build["package_hash"],
        reviews.store.state(p.project_id, build["build_id"])["sequence"],
        "archive",
        actor="operator",
        reason="archive failed candidate",
    )
    assert archived["review"]["archived"]
    new = studio.compile(p.project_id, build["revision_id"], policy)
    assert verifier.verify(p.project_id, new["build_id"], cases, policy)["status"] == "passed"
    approved = Approvals(studio).approve(
        p.project_id,
        new["build_id"],
        new["package_hash"],
        policy,
        actor="operator",
        reason="new verified build",
    )
    assert (
        Releases(studio).publish(p.project_id, approved["approval_id"], root, policy)["state"]
        == "published"
    )
    for relative, content in files.items():
        if relative.endswith(".tmp"):
            assert (root / relative).read_bytes() == content
    studio.close()


@pytest.mark.parametrize("stage", ["after_manifest", "after_pointer"])
def test_applied_complete_package_is_reconciled_without_reinstall(tmp_path, monkeypatch, stage):
    studio, p, _, _, _, _, _, root, pending, _ = failed_publish(tmp_path, monkeypatch, stage)
    recovery = ReleaseRecovery(studio)
    manifest = (root / "copy-example/flow.json").read_bytes()
    observed = recovery.inspect(p.project_id, pending["release_id"])
    assert observed["outcome"] == "applied"
    assert recovery.row(p.project_id, pending["release_id"])["status"] == "prepared"
    result = recovery.reconcile(
        p.project_id,
        pending["release_id"],
        observed["observation_hash"],
        actor="operator",
        reason="confirmed actual files",
    )
    assert result["status"] == "published"
    assert (root / "copy-example/flow.json").read_bytes() == manifest
    assert Releases(studio).current(root, "copy-example")["release_id"] == pending["release_id"]
    studio.close()


def test_identical_manifest_does_not_steal_previous_publication(tmp_path, monkeypatch):
    studio, p, _, _, _, _, _, root, pending, previous = failed_publish(
        tmp_path, monkeypatch, "after_manifest", existing=True
    )
    recovery = ReleaseRecovery(studio)
    observed = recovery.inspect(p.project_id, pending["release_id"])
    assert observed["outcome"] == "not_applied"
    recovery.reconcile(
        p.project_id,
        pending["release_id"],
        observed["observation_hash"],
        actor="operator",
        reason="keep prior decision",
    )
    assert Releases(studio).current(root, "copy-example")["release_id"] == previous["release_id"]
    studio.close()


def test_missing_previous_pointer_can_be_restored_without_committing_identical_attempt(
    tmp_path, monkeypatch
):
    studio, p, _, _, _, _, _, root, pending, previous = failed_publish(
        tmp_path, monkeypatch, "after_manifest", existing=True
    )
    pointer = root / ".studio_releases/copy-example/current.json"
    pointer.unlink()
    assert Releases(studio).current(root, "copy-example") is None
    recovery = ReleaseRecovery(studio)
    observed = recovery.inspect(p.project_id, pending["release_id"])
    assert observed["outcome"] == "not_applied"
    recovery.reconcile(
        p.project_id,
        pending["release_id"],
        observed["observation_hash"],
        actor="operator",
        reason="restore known previous pointer",
    )
    assert json.loads(pointer.read_text())["release_id"] == previous["release_id"]
    studio.close()


def test_busy_stale_and_corrupt_observations_never_apply(tmp_path, monkeypatch):
    studio, p, _, _, _, _, _, root, pending, _ = failed_publish(tmp_path, monkeypatch)
    recovery = ReleaseRecovery(studio)
    lock = root / ".studio_releases/copy-example/publish.lock"
    with FileLock(str(lock)):
        with pytest.raises(StudioError) as exc:
            recovery.inspect(p.project_id, pending["release_id"])
        assert exc.value.code == "PUBLISH_BUSY"
        with pytest.raises(StudioError) as exc:
            Releases(studio).current(root, "copy-example")
        assert exc.value.code == "PUBLISH_BUSY"
    observed = recovery.inspect(p.project_id, pending["release_id"])
    (root / "copy-example/flow.json").write_bytes(b"unrecognized")
    with pytest.raises(StudioError) as exc:
        recovery.reconcile(
            p.project_id,
            pending["release_id"],
            observed["observation_hash"],
            actor="operator",
            reason="stale must stop",
        )
    assert exc.value.code == "REVISION_CONFLICT"
    unknown = recovery.inspect(p.project_id, pending["release_id"])
    assert unknown["outcome"] == "unknown"
    with pytest.raises(StudioError):
        recovery.reconcile(
            p.project_id,
            pending["release_id"],
            unknown["observation_hash"],
            actor="operator",
            reason="cannot guess",
        )
    assert (root / "copy-example/flow.json").read_bytes() == b"unrecognized"
    studio.close()


def test_incomplete_wheel_is_not_marked_published(tmp_path, monkeypatch):
    studio, p, _, _, _, _, _, root, pending, _ = failed_publish(
        tmp_path, monkeypatch, "after_manifest"
    )
    manifest = json.loads((root / "copy-example/flow.json").read_text())
    (root / "copy-example" / manifest["package"]).write_bytes(b"corrupt wheel")
    recovery = ReleaseRecovery(studio)
    assert recovery.inspect(p.project_id, pending["release_id"])["outcome"] == "unknown"
    assert Releases(studio).current(root, "copy-example") is None
    assert recovery.row(p.project_id, pending["release_id"])["status"] == "prepared"
    studio.close()


def test_foreign_project_and_copied_target_cannot_claim_journal(tmp_path, monkeypatch):
    studio, p, _, _, _, _, _, root, pending, _ = failed_publish(tmp_path, monkeypatch)
    other = studio.projects.create("other")
    with pytest.raises(StudioError):
        ReleaseRecovery(studio).inspect(other.project_id, pending["release_id"])
    copied = tmp_path / "copied"
    shutil.copytree(root, copied)
    with pytest.raises(StudioError):
        Releases(studio).current(copied, "copy-example")
    studio.close()


def test_v013_upgrade_and_restore_keeps_existing_records(tmp_path):
    root = tmp_path / "old"
    root.mkdir()
    with sqlite3.connect(root / "studio.sqlite3") as db:
        for version in range(1, 14):
            for statement in MIGRATIONS[version]:
                db.execute(statement)
        db.execute(f"PRAGMA application_id={APPLICATION_ID}")
        db.execute("PRAGMA user_version=13")
        db.execute(
            "INSERT INTO studio_projects VALUES ('p','kept','{}','active',1,NULL,'before','before')"
        )
    repo = SQLiteRepository(root)
    assert repo.schema_version == SCHEMA_VERSION
    snapshot = next((root / "backups").glob("before-v013-*.sqlite3"))
    repo.close()
    restored = SQLiteRepository.restore(snapshot, tmp_path / "restored")
    with sqlite3.connect(restored) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 13
        assert db.execute("SELECT name FROM studio_projects").fetchone()[0] == "kept"


def test_audit_failure_rolls_back_abandonment(tmp_path, monkeypatch):
    studio, p, _, _, _, _, _, root, pending, _ = failed_publish(tmp_path, monkeypatch)
    recovery = ReleaseRecovery(studio)
    observed = recovery.inspect(p.project_id, pending["release_id"])
    studio.repository.connection.execute(
        "CREATE TRIGGER fail_reconciliation BEFORE INSERT ON studio_release_reconciliations BEGIN SELECT RAISE(ABORT, 'injected'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        recovery.reconcile(
            p.project_id,
            pending["release_id"],
            observed["observation_hash"],
            actor="operator",
            reason="fail atomically",
        )
    assert recovery.row(p.project_id, pending["release_id"])["status"] == "prepared"
    assert not (root / "copy-example/flow.json").exists()
    studio.close()


def test_unknown_files_are_preserved_and_still_block_overwrite(tmp_path, monkeypatch):
    studio, p, _, _, _, policy, approval, root, pending, _ = failed_publish(tmp_path, monkeypatch)
    note = root / "copy-example/user-note.txt"
    note.write_text("must keep")
    recovery = ReleaseRecovery(studio)
    observed = recovery.inspect(p.project_id, pending["release_id"])
    recovery.reconcile(
        p.project_id,
        pending["release_id"],
        observed["observation_hash"],
        actor="operator",
        reason="end only preparation",
    )
    with pytest.raises(StudioError, match="其他資料"):
        Releases(studio).publish(p.project_id, approval["approval_id"], root, policy)
    assert note.read_text() == "must keep"
    studio.close()


def test_applied_recovery_does_not_reinstate_revoked_approval(tmp_path, monkeypatch):
    studio, p, _, _, _, _, approval, root, pending, _ = failed_publish(
        tmp_path, monkeypatch, "after_manifest"
    )
    Approvals(studio).revoke(p.project_id, approval["approval_id"])
    recovery = ReleaseRecovery(studio)
    observed = recovery.inspect(p.project_id, pending["release_id"])
    assert observed["outcome"] == "applied" and observed["approval_unavailable"]
    recovery.reconcile(
        p.project_id,
        pending["release_id"],
        observed["observation_hash"],
        actor="operator",
        reason="record actual installed state",
    )
    assert Approvals(studio).get(p.project_id, approval["approval_id"])["revoked_at"]
    assert (root / "copy-example/flow.json").is_file()
    with pytest.raises(sqlite3.IntegrityError):
        studio.repository.connection.execute("DELETE FROM studio_release_reconciliations")
    studio.close()


@pytest.mark.parametrize("renamed", [False, True])
def test_interrupted_disable_records_actual_state_and_preserves_outputs(
    tmp_path, monkeypatch, renamed
):
    import workflow_studio.governance.release as module

    studio, p, _, _, _, policy, approval = verified_approval(tmp_path)
    root = tmp_path / "installed"
    releases = Releases(studio)
    published = releases.publish(p.project_id, approval["approval_id"], root, policy)
    output = root / "copy-example/result.txt"
    output.write_text("existing output")
    replace = module.os.replace

    def interrupt(source, target):
        if target.name.startswith("flow.disabled."):
            if renamed:
                replace(source, target)
            raise OSError("interrupted disable")
        return replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(module.os, "replace", interrupt)
        with pytest.raises(OSError):
            releases.disable(
                p.project_id,
                root,
                "copy-example",
                published["release_id"],
                actor="operator",
                reason="disable",
            )
    pending = [r for r in releases.history(p.project_id) if r["status"] == "prepared"][-1]
    recovery = ReleaseRecovery(studio)
    observed = recovery.inspect(p.project_id, pending["release_id"])
    assert observed["outcome"] == ("applied" if renamed else "not_applied")
    result = recovery.reconcile(
        p.project_id,
        pending["release_id"],
        observed["observation_hash"],
        actor="operator",
        reason="verify interrupted disable",
    )
    assert result["status"] == ("disabled" if renamed else "abandoned")
    assert (root / "copy-example/flow.json").exists() is not renamed
    assert releases.current(root, "copy-example")["state"] == (
        "disabled" if renamed else "published"
    )
    assert output.read_text() == "existing output"
    studio.close()


@pytest.mark.parametrize("newer_applied", [False, True])
def test_superseded_preparation_cannot_change_newer_attempt(tmp_path, monkeypatch, newer_applied):
    import workflow_studio.governance.release as module

    studio, p, _, _, _, policy, approval, root, pending, _ = failed_publish(tmp_path, monkeypatch)
    releases = Releases(studio)
    write = module.atomic_bytes

    def fail(path, data):
        if path.name == "flow.json" and path.parent.name == "copy-example":
            raise OSError("newer preparation interrupted")
        return write(path, data)

    with monkeypatch.context() as patch:
        if newer_applied:
            newer = releases.publish(p.project_id, approval["approval_id"], root, policy)
        else:
            patch.setattr(module, "atomic_bytes", fail)
            with pytest.raises(OSError):
                releases.publish(p.project_id, approval["approval_id"], root, policy)
            newer = json.loads((root / ".studio_releases/copy-example/journal.json").read_text())
    assert newer["release_id"] != pending["release_id"]
    before = {str(f.relative_to(root)): f.read_bytes() for f in root.rglob("*") if f.is_file()}
    recovery = ReleaseRecovery(studio)
    observed = recovery.inspect(p.project_id, pending["release_id"])
    assert observed["outcome"] == "not_applied"
    recovery.reconcile(
        p.project_id,
        pending["release_id"],
        observed["observation_hash"],
        actor="operator",
        reason="end superseded preparation",
    )
    assert recovery.row(p.project_id, newer["release_id"])["status"] == (
        "published" if newer_applied else "prepared"
    )
    for name, data in before.items():
        assert (root / name).read_bytes() == data
    studio.close()


def test_missing_target_is_not_assumed_unapplied(tmp_path, monkeypatch):
    studio, p, _, _, _, _, _, root, pending, _ = failed_publish(tmp_path, monkeypatch)
    moved = root.with_name("moved-installed")
    root.rename(moved)
    recovery = ReleaseRecovery(studio)
    with pytest.raises(StudioError, match="找不到原發布工作區"):
        recovery.inspect(p.project_id, pending["release_id"])
    assert recovery.row(p.project_id, pending["release_id"])["status"] == "prepared"
    assert not root.exists()
    studio.close()


def test_ui_model_reopens_and_reconciles_prepared_release(tmp_path, monkeypatch):
    from workflow_studio.conversation.session import StudioSession

    studio, p, _, _, _, _, _, _, pending, _ = failed_publish(tmp_path, monkeypatch)
    studio.close()
    model = StudioSession(tmp_path / "studio")
    model.select_project(p.project_id)
    observed = model.inspect_release(pending["release_id"])
    assert observed["outcome"] == "not_applied"
    model.reconcile_release(
        pending["release_id"], observed["observation_hash"], "operator", "verified actual files"
    )
    reopened = StudioSession(tmp_path / "studio")
    reopened.select_project(p.project_id)
    assert reopened.release_history()[-1]["status"] == "abandoned"
