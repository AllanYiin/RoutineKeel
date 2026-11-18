"""Twenty explicit publication faults, each protecting a real prior published package."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from compiler_helpers import execute_package
from gate_evidence import write_evidence
from test_governance import verified_approval
from workflow_studio.contracts import StudioError
from workflow_studio.governance.approvals import Approvals
from workflow_studio.governance.recovery import ReleaseRecovery
from workflow_studio.governance.release import Releases
from workflow_studio.verifier.golden_cases import GoldenCases

MATRIX_PATH = Path(__file__).parent / "fixtures/publication_faults.json"
MATRIX = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def tree_bytes(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def tree_hash(files):
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        digest.update(name.encode() + b"\0" + hashlib.sha256(content).digest())
    return digest.hexdigest()


def candidate_after_publication(tmp_path):
    studio, project, original, cases, verifier, policy, approval = verified_approval(tmp_path)
    root = tmp_path / "installed"
    first = Releases(studio).publish(project.project_id, approval["approval_id"], root, policy)
    output = root / "copy-example/outputs"
    output.mkdir()
    (output / "previous-result.txt").write_bytes(b"previous approved result must remain")
    payload = studio.projects.get_revision(project.project_id, original["revision_id"]).ir
    payload["revision"] += 1
    payload["nodes"][0]["timeout_seconds"] += 1
    revision = studio.save_ir(
        project.project_id, studio.projects.get(project.project_id).version, payload, policy
    )
    build = studio.compile(project.project_id, revision.revision_id, policy)
    assert (
        verifier.verify(project.project_id, build["build_id"], cases, policy)["status"] == "passed"
    )
    approved = Approvals(studio).approve(
        project.project_id,
        build["build_id"],
        build["package_hash"],
        policy,
        actor="fault-matrix reviewer",
        reason="second isolated candidate passed",
    )
    return studio, project, root, first, build, approved, policy, cases


def inject_race(point, studio, project, build, approval, policy, cases):
    identity = project.project_id
    reviewer = {"actor": "fault-matrix", "reason": point}
    if point == "approval_revoked":
        Approvals(studio).revoke(identity, approval["approval_id"], **reviewer)
    elif point == "review_returned":
        reviews = studio.reviews(policy)
        reviews.decide(
            identity,
            studio.projects.get(identity).version,
            build["build_id"],
            build["package_hash"],
            reviews.store.state(identity, build["build_id"])["sequence"],
            "return",
            **reviewer,
        )
    elif point == "project_archived":
        studio.projects.archive(identity, studio.projects.get(identity).version)
    elif point == "source_changed":
        evidence = studio.projects.get_revision(identity, build["revision_id"]).ir["evidence"][0]
        studio.sources.describe(
            identity,
            evidence["source_id"],
            studio.projects.get(identity).version,
            "concurrent source change",
        )
    elif point == "case_added":
        catalog = GoldenCases(studio)
        specification = catalog.get(identity, cases[0]["case_id"])["specification"]
        specification["name"] = "new required case during publish"
        catalog.save(identity, studio.projects.get(identity).version, specification)
    elif point == "tool_disabled":
        item = studio.tools.get("file.copy", "1.0.0")
        studio.tools.set_status(
            "file.copy", "1.0.0", item["record_version"], "disabled", **reviewer
        )
    else:
        raise AssertionError(point)


@pytest.mark.parametrize("fault", MATRIX, ids=[item["id"] + "-" + item["point"] for item in MATRIX])
def test_previous_publication_survives_twenty_controlled_faults(tmp_path, monkeypatch, fault):
    import workflow_studio.governance.release as module

    assert len(MATRIX) == len({f["point"] for f in MATRIX}) == 20
    studio, p, root, first, build, approved, policy, cases = candidate_after_publication(tmp_path)
    try:
        releases = Releases(studio)
        records = root / ".studio_releases/copy-example"
        old_snapshot = records / first["release_id"]
        snapshot_before = tree_bytes(old_snapshot)
        output_before = tree_bytes(root / "copy-example/outputs")
        manifest_before = (root / "copy-example/flow.json").read_bytes()
        old_manifest = json.loads(manifest_before)
        old_wheel = root / "copy-example" / old_manifest["package"]
        wheel_before = old_wheel.read_bytes()
        hit, point = [], fault["point"]
        write = module.atomic_bytes
        trigger = None
        if point == "prepared_insert":
            trigger = (
                "BEFORE INSERT ON studio_releases WHEN NEW.build_id='" + build["build_id"] + "'"
            )
        elif point == "release_finalize":
            trigger = (
                "BEFORE UPDATE ON studio_releases WHEN NEW.build_id='"
                + build["build_id"]
                + "' AND NEW.status='published'"
            )
        elif point == "revision_finalize":
            trigger = (
                "BEFORE UPDATE ON studio_revisions WHEN NEW.revision_id='"
                + build["revision_id"]
                + "' AND NEW.status='published'"
            )
        if trigger:
            studio.repository.connection.execute(
                "CREATE TRIGGER injected_matrix_failure "
                + trigger
                + " BEGIN SELECT RAISE(ABORT, 'fault:"
                + fault["id"]
                + "'); END"
            )

        def fail(path, data):
            new_snapshot = path.parent.parent == records and path.parent.name != first["release_id"]
            installed = path.parent == root / "copy-example"
            new_pointer = (
                path == records / "current.json"
                and json.loads(data)["build_id"] == build["build_id"]
            )
            journal = path == records / "journal.json"
            before = (
                (point == "snapshot_readme" and new_snapshot and path.name == "README.md")
                or (point == "snapshot_manifest" and new_snapshot and path.name == "flow.json")
                or (point == "journal_before" and journal)
                or (point == "wheel_before" and installed and path.suffix == ".whl")
                or (point == "manifest_before" and installed and path.name == "flow.json")
                or (point == "pointer_before" and new_pointer)
            )
            partial = path.suffix == ".whl" and (
                (point == "snapshot_wheel_partial" and new_snapshot)
                or (point == "wheel_partial" and installed)
            )
            if before or partial:
                hit.append(point)
                if partial:
                    path.with_name(path.name + "." + "f" * 32 + ".tmp").write_bytes(data[:24])
                raise OSError("fault:" + fault["id"])
            write(path, data)
            after = (
                (point == "journal_after" and journal)
                or (point == "manifest_after" and installed and path.name == "flow.json")
                or (point == "pointer_after" and new_pointer)
            )
            if after:
                hit.append(point)
                raise OSError("fault:" + fault["id"])
            if (
                fault["error"] not in {"io", "sqlite"}
                and installed
                and path.suffix == ".whl"
                and not hit
            ):
                hit.append(point)
                inject_race(point, studio, p, build, approved, policy, cases)

        try:
            with monkeypatch.context() as patch:
                patch.setattr(module, "atomic_bytes", fail)
                with pytest.raises((OSError, sqlite3.IntegrityError, StudioError)) as raised:
                    releases.publish(
                        p.project_id,
                        approved["approval_id"],
                        root,
                        policy,
                        expected_current=first["release_id"],
                    )
        finally:
            if trigger:
                studio.repository.connection.execute("DROP TRIGGER injected_matrix_failure")
        error = raised.value
        if fault["error"] in {"io", "sqlite"}:
            assert "fault:" + fault["id"] in str(error)
            if trigger:
                hit.append(point)
        else:
            assert isinstance(error, StudioError) and error.code == fault["error"]
        assert hit == [point], "The intended fault point must actually execute exactly once"
        assert tree_bytes(old_snapshot) == snapshot_before
        assert tree_bytes(root / "copy-example/outputs") == output_before
        assert old_wheel.read_bytes() == wheel_before
        if fault["boundary"] == "before_switch":
            assert (root / "copy-example/flow.json").read_bytes() == manifest_before

        pending = [
            row
            for row in releases.history(p.project_id)
            if row["build_id"] == build["build_id"] and row["status"] == "prepared"
        ]
        outcome = "no_preparation"
        if pending:
            assert len(pending) == 1
            recovery = ReleaseRecovery(studio)
            observed = recovery.inspect(p.project_id, pending[0]["release_id"])
            outcome = observed["outcome"]
            assert outcome == ("applied" if fault["boundary"] == "after_switch" else "not_applied")
            recovery.reconcile(
                p.project_id,
                pending[0]["release_id"],
                observed["observation_hash"],
                actor="fault-matrix",
                reason="verified physical state after controlled fault",
            )
        if fault["boundary"] == "after_switch":
            assert (
                pending and releases.current(root, "copy-example")["build_id"] == build["build_id"]
            )
            restored = releases.rollback(p.project_id, first["release_id"], policy)
            assert restored["build_id"] == first["build_id"]
        else:
            assert releases.current(root, "copy-example")["release_id"] == first["release_id"]
        assert (root / "copy-example/flow.json").read_bytes() == manifest_before
        assert tree_bytes(old_snapshot) == snapshot_before
        assert tree_bytes(root / "copy-example/outputs") == output_before
        assert old_wheel.read_bytes() == wheel_before
        probe = execute_package(
            root / "copy-example", tmp_path / "runtime-probe", b"previous version still works"
        )
        assert probe["status"] == "complete" and probe["token_usage"]["registered_requests"] == 0
        assert (
            tmp_path / "runtime-probe/output/artifacts/result/artifacts/result.txt"
        ).read_bytes() == b"previous version still works"
        assert studio.repository.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert studio.repository.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        write_evidence(
            tmp_path,
            "publication-fault-" + fault["id"],
            {
                "fault": fault,
                "matrix_sha256": hashlib.sha256(MATRIX_PATH.read_bytes()).hexdigest(),
                "injected_count": len(hit),
                "recovery_outcome": outcome,
                "previous_release_id": first["release_id"],
                "snapshot_sha256_before": tree_hash(snapshot_before),
                "snapshot_sha256_after": tree_hash(tree_bytes(old_snapshot)),
                "outputs_preserved": True,
                "previous_version_replayed": True,
                "probe_run_id": probe["run_id"],
                "integrity_check": "ok",
                "passed": True,
            },
        )
    finally:
        studio.close()
