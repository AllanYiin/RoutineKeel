import hashlib
import json
import zipfile
from pathlib import Path

import pytest
from flow_base.core.service import WorkflowService
from workflow_studio.contracts import StudioError
from workflow_studio.conversation.facade import dispatch
from workflow_studio.service import StudioService


def draft(studio, root, *, skills=None):
    state = studio.interviews.create("分流", "依輸入選擇校閱方式")
    graph = json.loads((Path(__file__).parents[1] / "fixtures/graph_v2/route.json").read_bytes())
    graph["provenance"] = {}
    graph["quality_contract"] = {
        "success_criteria": ["原文一致"],
        "golden_cases": [
            {"inputs": {"message": "a", "needs_review": False}, "expected": {"result": "a"}}
        ],
    }
    return state, {
        "project_id": state["project_id"],
        "expected_revision": state["revision"],
        "request_id": "preview",
        "graph": graph,
        "design": {"rules": "保留原文"},
        "skills": skills or [],
    }


def approved(proposal):
    return {
        "project_id": proposal["project_id"],
        "expected_revision": proposal["revision"],
        "request_id": "export",
        "proposal_id": proposal["proposal_id"],
        "decision": "approve",
        "user_text": "同意這份設計",
        "shown_sections": {
            key: item["sha256"] for key, item in proposal["review_sections"].items()
        },
        **({"review_hash": proposal["review_hash"]} if "review_hash" in proposal else {}),
    }


def finalize(studio, root, proposal, review):
    return dispatch(
        studio,
        root,
        "finalize_workflow_design",
        {
            "project_id": proposal["project_id"],
            "expected_revision": proposal["revision"],
            "request_id": "finalize",
            "draft_id": proposal["proposal_id"],
            "review": review,
            "draft_reply": "初稿可進入定稿",
            "shown_sections": {
                key: item["sha256"] for key, item in proposal["review_sections"].items()
            },
        },
    )


def test_real_importer_round_trip_immutable_export_and_reopen(tmp_path, workflow_design_review):
    with StudioService.open_workspace(tmp_path) as studio:
        state, params = draft(studio, tmp_path)
        attach = studio.sources.import_text(
            state["project_id"], state["revision"], "請只在交付時校閱", name="SKILL.md"
        )
        params.update(
            expected_revision=studio.projects.get(state["project_id"]).version,
            skills=[{"name": "review", "source_id": attach.source_id, "version": attach.version}],
        )
        proposal = dispatch(studio, tmp_path, "prepare_blueprint", params)
        review = workflow_design_review(proposal)
        review["skills"]["review"] = ["manual"]
        proposal = finalize(studio, tmp_path, proposal, review)
        assert not (tmp_path / "blueprints").exists()
        result = dispatch(studio, tmp_path, "export_blueprint", approved(proposal))
        path = Path(result["path"])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == result["sha256"]
        with zipfile.ZipFile(path) as z:
            assert set(z.namelist()) == {"design/blueprint.json", "design/skills/review/SKILL.md"}
            assert z.read("design/skills/review/SKILL.md").decode() == "請只在交付時校閱"
            graph = json.loads(z.read("design/blueprint.json"))
            assert graph["provenance"]["proposal_id"] == proposal["proposal_id"]
            assert "同意這份設計" not in z.read("design/blueprint.json").decode()
        assert dispatch(studio, tmp_path, "export_blueprint", approved(proposal)) == result
    with StudioService.open_workspace(tmp_path) as studio:
        assert (
            dispatch(
                studio,
                tmp_path,
                "get_blueprint",
                {"project_id": state["project_id"], "sha256": result["sha256"]},
            )
            == result
        )
        path.unlink()
        restored = dispatch(
            studio,
            tmp_path,
            "get_blueprint",
            {"project_id": state["project_id"], "sha256": result["sha256"]},
        )
        assert (
            restored == result and hashlib.sha256(path.read_bytes()).hexdigest() == result["sha256"]
        )
    runtime = WorkflowService(tmp_path)
    key = runtime.install_workflow(archive=path)["draft_id"]
    state = runtime.resolve_blueprint_capabilities(key)
    candidate = runtime.install_workflow(
        draft_id=key,
        resolution={"capability_fingerprint": state["fingerprint"]},
    )
    runtime.install_workflow(
        draft_id=key,
        build_id=candidate["build_id"],
        install_hash=candidate["install_hash"],
    )
    payload = tmp_path / "message.json"
    payload.write_text(json.dumps({"inputs": {"message": "a", "needs_review": True}}))
    waiting = runtime.run_workflow_sync({"task": "routing", "source": payload})["blueprint"]
    assert waiting["pending"]["skill_documents"] == {"review": {"SKILL.md": "請只在交付時校閱"}}


def test_declined_stale_incomplete_or_private_design_never_delivered(
    tmp_path, workflow_design_review
):
    with StudioService.open_workspace(tmp_path) as studio:
        state, params = draft(studio, tmp_path)
        incomplete = dispatch(studio, tmp_path, "prepare_blueprint", {**params, "design": {}})
        assert "flowchart" in incomplete["draft_design"]["diagram"]
        with pytest.raises(StudioError, match="缺少"):
            finalize(studio, tmp_path, incomplete, workflow_design_review(incomplete))
        params.update(expected_revision=incomplete["revision"], request_id="revised-preview")
        p = dispatch(studio, tmp_path, "prepare_blueprint", params)
        decision = approved(p)
        assert (
            dispatch(
                studio,
                tmp_path,
                "export_blueprint",
                {**decision, "decision": "decline", "user_text": "修改後再說"},
            )["status"]
            == "declined"
        )
        with pytest.raises(StudioError):
            dispatch(studio, tmp_path, "export_blueprint", {**decision, "request_id": "late"})
        assert not (tmp_path / "blueprints").exists()
        params.update(
            expected_revision=studio.projects.get(state["project_id"]).version, request_id="private"
        )
        params["design"]["rules"] = {"password": "private-fixture"}
        with pytest.raises(StudioError, match="認證"):
            dispatch(studio, tmp_path, "prepare_blueprint", params)
        assert not (tmp_path / "blueprints").exists()


def test_changed_attachment_invalidates_summary_and_cannot_publish_wrong_version(
    tmp_path,
    workflow_design_review,
):
    with StudioService.open_workspace(tmp_path) as studio:
        state, params = draft(studio, tmp_path)
        pid = state["project_id"]
        original = studio.sources.import_text(pid, state["revision"], "原規則", name="SKILL.md")
        params.update(
            expected_revision=studio.projects.get(pid).version,
            skills=[{"name": "rules", "source_id": original.source_id, "version": 1}],
        )
        proposal = dispatch(studio, tmp_path, "prepare_blueprint", params)
        proposal = finalize(studio, tmp_path, proposal, workflow_design_review(proposal))
        studio.sources.import_text(
            pid, proposal["revision"], "修改規則", name="SKILL.md", source_id=original.source_id
        )
        with pytest.raises(StudioError, match="已有更新"):
            dispatch(studio, tmp_path, "export_blueprint", approved(proposal))
        with pytest.raises(StudioError, match="更新或封存"):
            dispatch(
                studio,
                tmp_path,
                "prepare_blueprint",
                {
                    **params,
                    "expected_revision": studio.projects.get(pid).version,
                    "request_id": "new",
                },
            )
        assert not (tmp_path / "blueprints").exists()
