import json

import pytest
from test_verifier import verification_case
from workflow_studio.contracts import StudioError
from workflow_studio.governance.approvals import Approvals
from workflow_studio.governance.release import Releases


def verified_approval(tmp_path):
    studio, project, build, cases, verifier, policy = verification_case(tmp_path)
    report = verifier.verify(project.project_id, build["build_id"], cases, policy)
    assert report["status"] == "passed"
    approval = Approvals(studio).approve(
        project.project_id,
        build["build_id"],
        build["package_hash"],
        policy,
        actor="fixture user",
        reason="確認固定案例符合目標",
    )
    return studio, project, build, cases, verifier, policy, approval


def test_interview_project_cannot_promote_through_legacy_release_api(tmp_path):
    studio, p, build, _cases, verifier, policy, approval = verified_approval(tmp_path)
    try:
        report = verifier.get(p.project_id, approval["report_id"])
        assert report["builder_receipt"]["package_hash"] == build["package_hash"]
        studio.interviews.start(p.project_id, studio.projects.get(p.project_id).version)
        target = tmp_path / "alternate"
        with pytest.raises(StudioError, match="對話專案"):
            Releases(studio).publish(p.project_id, approval["approval_id"], target, policy)
        assert not target.exists()
    finally:
        studio.close()


@pytest.mark.parametrize("nested", [False, True])
def test_publish_idempotency_update_rollback_and_preserve_outputs(tmp_path, nested):
    studio, p, build, cases, verifier, policy, approval = verified_approval(tmp_path)
    target = tmp_path / "installed"
    if nested:
        (target / "workflows").mkdir(parents=True)
    releases = Releases(studio)
    first = releases.publish(p.project_id, approval["approval_id"], target, policy)
    assert releases.publish(p.project_id, approval["approval_id"], target, policy) == first
    folder = (
        target / "workflows" / ("FLOW_" + first["workflow_id"])
        if nested
        else target / first["workflow_id"]
    )
    (folder / "outputs").mkdir()
    (folder / "outputs/keep.txt").write_text("user result")
    payload = studio.projects.get_revision(p.project_id, build["revision_id"]).ir
    payload["revision"] = 2
    payload["nodes"][0]["timeout_seconds"] = 90
    current = studio.projects.get(p.project_id)
    revision = studio.save_ir(p.project_id, current.version, payload, policy)
    second_build = studio.compile(p.project_id, revision.revision_id, policy)
    assert (
        verifier.verify(p.project_id, second_build["build_id"], cases, policy)["status"] == "passed"
    )
    approved = Approvals(studio).approve(
        p.project_id,
        second_build["build_id"],
        second_build["package_hash"],
        policy,
        actor="fixture user",
        reason="重新驗收",
    )
    second = releases.publish(
        p.project_id, approved["approval_id"], target, policy, expected_current=first["release_id"]
    )
    assert second["release_id"] != first["release_id"]
    rolled_back = releases.rollback(p.project_id, first["release_id"], policy)
    assert rolled_back["build_id"] == build["build_id"]
    assert (folder / "outputs/keep.txt").read_text() == "user result"
    assert len(list(folder.glob("*.whl"))) == 2
    assert json.loads((folder / "flow.json").read_text(encoding="utf-8"))["version"] == "0.1.1"
    disabled = releases.disable(
        p.project_id, target, first["workflow_id"], rolled_back["release_id"]
    )
    assert disabled["state"] == "disabled"
    assert not (folder / "flow.json").exists()
    restored = releases.rollback(p.project_id, first["release_id"], policy)
    assert restored["state"] == "published"
    assert (folder / "outputs/keep.txt").read_text() == "user result"
    studio.close()


def test_unverified_revoked_and_changed_project_cannot_publish(tmp_path):
    studio, p, build, cases, verifier, policy = verification_case(tmp_path)
    with pytest.raises(StudioError):
        Approvals(studio).approve(
            p.project_id,
            build["build_id"],
            build["package_hash"],
            policy,
            actor="user",
            reason="not verified",
        )
    assert verifier.verify(p.project_id, build["build_id"], cases, policy)["status"] == "passed"
    approval = Approvals(studio).approve(
        p.project_id,
        build["build_id"],
        build["package_hash"],
        policy,
        actor="user",
        reason="verified",
    )
    current = studio.projects.get(p.project_id)
    studio.projects.update(
        p.project_id,
        current.version,
        goal={"description": "different", "success_criteria": ["changed"]},
    )
    with pytest.raises(StudioError) as caught:
        Releases(studio).publish(
            p.project_id, approval["approval_id"], tmp_path / "installed", policy
        )
    assert caught.value.code == "REVISION_CONFLICT"
    Approvals(studio).revoke(p.project_id, approval["approval_id"])
    with pytest.raises(StudioError) as caught:
        Releases(studio).publish(
            p.project_id, approval["approval_id"], tmp_path / "installed", policy
        )
    assert caught.value.code == "APPROVAL_REQUIRED"
    studio.close()


def test_failed_first_manifest_switch_can_retry_without_overwriting_other_data(
    tmp_path, monkeypatch
):
    import workflow_studio.governance.release as release_module

    studio, p, build, _, _, policy, approval = verified_approval(tmp_path)
    original = release_module.atomic_bytes

    def fail_manifest(path, data):
        if path.name == "flow.json" and path.parent.name == "copy-example":
            raise OSError("injected disk failure")
        return original(path, data)

    monkeypatch.setattr(release_module, "atomic_bytes", fail_manifest)
    target = tmp_path / "installed"
    with pytest.raises(OSError):
        Releases(studio).publish(p.project_id, approval["approval_id"], target, policy)
    assert not (target / "copy-example/flow.json").exists()
    monkeypatch.setattr(release_module, "atomic_bytes", original)
    result = Releases(studio).publish(p.project_id, approval["approval_id"], target, policy)
    assert result["build_id"] == build["build_id"]
    studio.close()
