import pytest
from compiler_helpers import build_ir
from test_governance import verified_approval
from workflow_studio.contracts import StudioError
from workflow_studio.conversation.session import StudioSession
from workflow_studio.governance.approvals import Approvals
from workflow_studio.governance.release import Releases
from workflow_studio.llm.streaming import EventStore
from workflow_studio.project.history import differences
from workflow_studio.service import StudioService


def test_history_reuse_preserves_original_and_orders_versions_after_discard(tmp_path):
    studio, p, original, policy = build_ir(tmp_path)
    new = studio.history.reuse(p.project_id, original.revision_id, 3, policy)
    assert new.ir["revision"] == 2
    assert studio.projects.get_revision(p.project_id, original.revision_id) == original
    assert studio.history.compare(p.project_id, original.revision_id, new.revision_id)[
        "changes"
    ] == [{"path": ["revision"], "operation": "changed", "before": 1, "after": 2}]
    project = studio.history.discard(p.project_id, new.revision_id, 4)
    assert project.current_revision_id == original.revision_id
    third = studio.history.reuse(p.project_id, new.revision_id, project.version, policy)
    assert third.ir["revision"] == 3
    assert studio.projects.get_revision(p.project_id, new.revision_id).discarded
    assert (
        EventStore(studio.repository).read(p.project_id)[-1]["source_revision_id"]
        == new.revision_id
    )
    studio.close()
    with StudioService.open_workspace(tmp_path / "studio") as reopened:
        assert reopened.projects.get_revision(p.project_id, third.revision_id) == third
        assert len(reopened.projects.revisions(p.project_id, include_discarded=True)) == 3


def test_goal_restore_is_explicit_and_rolls_back_if_current_tool_not_allowed(tmp_path):
    studio, p, original, policy = build_ir(tmp_path)
    changed = studio.projects.update(
        p.project_id, 3, goal={"description": "新目標", "success_criteria": ["新規則"]}
    )
    with pytest.raises(StudioError, match="還原目標"):
        studio.history.reuse(p.project_id, original.revision_id, changed.version, policy)
    tool = studio.tools.get("file.copy", "1.0.0")
    studio.tools.set_status("file.copy", "1.0.0", tool["record_version"], "disabled")
    with pytest.raises(StudioError):
        studio.history.reuse(
            p.project_id, original.revision_id, changed.version, policy, restore_goal=True
        )
    assert studio.projects.get(p.project_id) == changed
    assert len(studio.projects.revisions(p.project_id)) == 1
    tool = studio.tools.get("file.copy", "1.0.0")
    studio.tools.set_status("file.copy", "1.0.0", tool["record_version"], "active")
    restored = studio.history.reuse(
        p.project_id, original.revision_id, changed.version, policy, restore_goal=True
    )
    assert restored.ir["goal"] == studio.projects.get(p.project_id).goal == original.ir["goal"]
    studio.close()


def test_approved_and_published_history_cannot_be_discarded_or_mutated(tmp_path):
    studio, p, build, _cases, verifier, policy, approval = verified_approval(tmp_path)
    current = studio.projects.get(p.project_id)
    with pytest.raises(StudioError) as exc:
        studio.history.discard(p.project_id, build["revision_id"], current.version)
    assert exc.value.code == "REVISION_IMMUTABLE"
    target = tmp_path / "installed"
    release = Releases(studio).publish(p.project_id, approval["approval_id"], target, policy)
    manifest = target / release["workflow_id"] / "flow.json"
    before = manifest.read_bytes()
    old = studio.projects.get_revision(p.project_id, build["revision_id"])
    Approvals(studio).revoke(p.project_id, approval["approval_id"])
    with pytest.raises(StudioError):
        studio.history.discard(p.project_id, build["revision_id"], current.version)
    new = studio.history.reuse(p.project_id, old.revision_id, current.version, policy)
    assert new.ir["revision"] == 2
    assert studio.projects.get_revision(p.project_id, old.revision_id) == old
    assert manifest.read_bytes() == before
    assert verifier.get(p.project_id, approval["report_id"])["status"] == "passed"
    studio.close()


def test_reuse_rejects_stale_editor_and_cross_project_revision(tmp_path):
    studio, p, original, policy = build_ir(tmp_path)
    other = studio.projects.create("另一專案")
    with pytest.raises(StudioError):
        studio.history.reuse(other.project_id, original.revision_id, other.version, policy)
    studio.projects.update(p.project_id, 3, name="已被修改")
    with pytest.raises(StudioError) as exc:
        studio.history.reuse(p.project_id, original.revision_id, 3, policy)
    assert exc.value.code == "REVISION_CONFLICT"
    with pytest.raises(StudioError):
        studio.history.compare(other.project_id, original.revision_id, original.revision_id)
    studio.close()


def test_raw_draft_can_be_copied_and_comparison_keeps_types(tmp_path):
    with StudioService.open_workspace(tmp_path) as studio:
        p = studio.projects.create("未完成草稿")
        old = studio.projects.add_revision(p.project_id, 1, {"analysis": "待補規則", "ir": None})
        from workflow_studio.registry.policy import ToolPolicy

        new = studio.history.reuse(p.project_id, old.revision_id, 2, ToolPolicy("windows"))
        assert new.status == "draft" and new.ir == old.ir
    diff = differences({"nodes": [{"x": True}], "removed": 1}, {"nodes": [{"x": 1}], "added": 2})
    assert any(
        c["path"] == ["nodes", "0", "x"] and type(c["before"]) is bool for c in diff["changes"]
    )
    assert {c["operation"] for c in diff["changes"]} == {"changed", "added", "removed"}
    assert differences({"x": list(range(600))}, {"x": [None] * 600})["truncated"]


def test_ui_history_actions_invalidate_cached_build_and_approval(tmp_path):
    studio, p, revision, _policy = build_ir(tmp_path)
    studio.close()
    model = StudioSession(tmp_path / "studio")
    model.select_project(p.project_id)
    model.build_id = "old-build"
    model.approval_id = "old-approval"
    changed = model.reuse_revision(revision.revision_id, model.project().version)
    assert model.build_id is model.approval_id is None
    assert len(model.revision_history()) == 2
    assert model.compare_revisions(revision.revision_id, changed.revision_id)["changes"]
    model.discard_revision(changed.revision_id, model.project().version)
    assert model.revision() == revision


def test_discarded_verified_draft_cannot_be_newly_approved(tmp_path):
    from test_verifier import verification_case

    studio, p, build, cases, verifier, policy = verification_case(tmp_path)
    assert verifier.verify(p.project_id, build["build_id"], cases, policy)["status"] == "passed"
    studio.history.discard(
        p.project_id, build["revision_id"], studio.projects.get(p.project_id).version
    )
    with pytest.raises(StudioError) as exc:
        Approvals(studio).approve(
            p.project_id,
            build["build_id"],
            build["package_hash"],
            policy,
            actor="fixture",
            reason="discarded must not be approved",
        )
    assert exc.value.code == "REVISION_IMMUTABLE"
    studio.close()


def test_discard_during_approval_validation_cannot_race_the_final_insert(tmp_path, monkeypatch):
    import workflow_studio.governance.approvals as approval_module
    from test_verifier import verification_case

    studio, p, build, cases, verifier, policy = verification_case(tmp_path)
    assert verifier.verify(p.project_id, build["build_id"], cases, policy)["status"] == "passed"

    def discard_at_validation(ir):
        studio.history.discard(
            p.project_id, build["revision_id"], studio.projects.get(p.project_id).version
        )
        return "low"

    monkeypatch.setattr(approval_module, "risk_level", discard_at_validation)
    with pytest.raises(StudioError) as exc:
        Approvals(studio).approve(
            p.project_id,
            build["build_id"],
            build["package_hash"],
            policy,
            actor="fixture",
            reason="racing discard",
        )
    assert exc.value.code == "REVISION_IMMUTABLE"
    assert (
        studio.repository.connection.execute("SELECT COUNT(*) FROM studio_approvals").fetchone()[0]
        == 0
    )
    studio.close()
