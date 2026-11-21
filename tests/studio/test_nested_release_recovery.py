import pytest
from test_release_recovery import failed_publish
from workflow_studio.governance.recovery import ReleaseRecovery
from workflow_studio.governance.release import Releases


@pytest.mark.parametrize("stage", ["before_manifest", "after_manifest"])
def test_nested_publish_recovery_observes_actual_manifest(tmp_path, monkeypatch, stage):
    (tmp_path / "installed/workflows").mkdir(parents=True)
    studio, project, _, _, _, _, _, root, pending, _ = failed_publish(tmp_path, monkeypatch, stage)
    recovery = ReleaseRecovery(studio)
    observed = recovery.inspect(project.project_id, pending["release_id"])
    applied = stage == "after_manifest"
    assert observed["outcome"] == ("applied" if applied else "not_applied")
    result = recovery.reconcile(
        project.project_id,
        pending["release_id"],
        observed["observation_hash"],
        actor="test operator",
        reason="recover nested workspace",
    )
    assert result["status"] == ("published" if applied else "abandoned")
    assert (root / "workflows/FLOW_copy-example/flow.json").is_file() is applied
    assert not (root / "copy-example").exists()
    assert bool(Releases(studio).current(root, "copy-example")) is applied
    studio.close()
