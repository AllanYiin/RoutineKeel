import io
import json
import sys
import zipfile

import pytest
from flow_base.core.service import WorkflowService
from flow_base.graph.qualification import BindingReview
from flow_base.runtime import FlowError
from flow_base.studio import studio_request
from test_pattern_composition import executable_pattern, options
from test_pattern_extraction import chain
from test_pattern_qualification import write_pack
from test_pattern_registry import decision
from workflow_studio.contracts import StudioError
from workflow_studio.patterns.composer import compose
from workflow_studio.patterns.evolution import attribution
from workflow_studio.patterns.qualification import qualify
from workflow_studio.service import StudioService


def _pack(path, pattern, version):
    path.mkdir()
    zip_path = write_pack(path, pattern)
    original = io.BytesIO(zip_path.read_bytes())
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(zip_path, "w") as target:
        for member in source.namelist():
            raw = source.read(member)
            if member == "pack.json":
                manifest = json.loads(raw)
                manifest["pack"]["version"] = version
                raw = json.dumps(manifest)
            target.writestr(member, raw)
    return zip_path


def _workflow(
    service, name, version, pattern, pack, trial, *, rehearsal=None, bad=False, review=None
):
    valid = {"inputs": {"text": "BASE"}, "expected": {"result": "BASE"}}
    defect = {"inputs": {"text": "Mixed case"}, "expected": {"result": "Mixed case"}}
    base = chain()
    base["workflow"].update(id=name, version=version, revision=2 if rehearsal else 1)
    base["quality_contract"]["golden_cases"] = [valid] + ([defect] if rehearsal else [])
    if bad:
        base["nodes"][-1]["capability"] = "text.upper"
    original_upper = pattern.graph.nodes[0].capability == "text.upper"
    cases = [
        {
            "id": label,
            "inputs": {"source": value},
            "expected": {"result": value.upper() if original_upper else value},
        }
        for label, value in [("one", "Case"), ("two", "Source")]
    ]
    base["quality_contract"]["adapter_cases"] = {"copier": cases}
    payload = pattern.model_dump(mode="json")
    parameters = options(payload, namespace="preserve_" + version[0])
    parameters.update(
        pack=pack,
        pattern_hash=trial["pattern_hash"],
        qualification_hash=trial["qualification_hash"],
    )
    root = service.workspace.root
    if rehearsal and not bad:
        project = studio_request(root, "create", {"name": f"重播 {name}", "task": "保留原文字"})
        pid = project["project_id"]
        offer = studio_request(
            root,
            "recommend_patterns",
            {
                "project_id": pid,
                "expected_revision": project["revision"],
                "request_id": "use-old",
                "context": {
                    "intent": "保留原文",
                    "category": "transcript",
                    "facts": ["source_text"],
                    "available_inputs": {"text": {"type": "string"}},
                },
            },
        )
        (current,) = offer["recommendations"]
        accepted = studio_request(
            root,
            "decide_pattern",
            {
                "project_id": pid,
                "expected_revision": offer["revision"],
                "request_id": "accept-old",
                "recommendation_id": current["recommendation_id"],
                "decision": "accepted",
                "user_text": "同意在草稿採用目前版本",
            },
        )
        base["quality_contract"]["success_criteria"] = ["保留附帶大小寫的原文"]
        draft = studio_request(
            root,
            "prepare_blueprint",
            {
                "project_id": pid,
                "expected_revision": accepted["revision"],
                "request_id": "draft",
                "graph": base,
                "design": {"rules": "保留原始大小寫"},
            },
        )
        proposal = studio_request(
            root,
            "compose_pattern",
            {
                "project_id": pid,
                "expected_revision": draft["revision"],
                "request_id": "rehearse",
                "draft_id": draft["proposal_id"],
                "decision_id": current["recommendation_id"],
                "pattern_ref": pattern.pattern.ref,
                "insert_location": parameters["location"],
                "port_map": parameters["mapping"],
                "parameter_bindings": {},
                "improvement_id": rehearsal,
                "target_trial": trial["qualification_hash"],
            },
        )
        assert proposal["proposal"]["graph"]["provenance"]["pattern_rehearsal"] == rehearsal
        final = studio_request(
            root,
            "finalize_workflow_design",
            {
                "project_id": pid,
                "expected_revision": proposal["revision"],
                "request_id": "finalize",
                "draft_id": proposal["proposal_id"],
                "review": review(proposal["proposal"]),
                "draft_reply": "初稿可進入定稿",
                "shown_sections": {key: item["sha256"] for key, item in proposal["proposal"]["review_sections"].items()},
            },
        )
        exported = studio_request(
            root,
            "export_blueprint",
            {
                "project_id": pid,
                "expected_revision": final["revision"],
                "request_id": "freeze",
                "proposal_id": final["proposal_id"],
                "review_hash": final["review_hash"],
                "shown_sections": {key: item["sha256"] for key, item in final["review_sections"].items()},
                "decision": "approve",
                "user_text": "確認匯出這份回歸候選",
            },
        )
        from pathlib import Path

        path = Path(exported["path"])
    else:
        graph = compose(base, pattern, **parameters)["graph"]
        if rehearsal:
            graph["provenance"]["pattern_rehearsal"] = rehearsal
        path = root / f"{name}-{version}-{bad}.zip"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("design/blueprint.json", json.dumps(graph))
    key = service.install_workflow(archive=path)["draft_id"]
    state = service.resolve_blueprint_capabilities(key)
    choices = {row["node"]: row["candidates"][0]["adapter"] for row in state["portable_decisions"]}
    preview = service.preview_binding_qualification(key, choices)
    assert (
        service.qualify_blueprint_bindings(
            key, choices, BindingReview(preview["request_hash"], "simulated consent for local test")
        )["status"]
        == "qualified"
    )
    return key, defect


def _feedback(service, workflow, folder, text, tag, *, scope="this_run"):
    path = folder / f"input-{workflow}-{tag}.json"
    path.write_text(json.dumps({"inputs": {"text": text}}))
    outcome = service.run_workflow_sync({"task": workflow, "source": path})
    session = outcome["blueprint"]["session_id"]
    preview = service.workflow_quality(session)
    result = service.workflow_quality(
        session,
        "feedback",
        {
            "expected_revision": preview["revision"],
            "request_id": tag,
            "preview_id": preview["preview_id"],
            "decision": "correct",
            "reason": "原文大小寫應保留",
            "user_text": "請保留原文",
            "outputs": {"result": text},
            "tags": [tag, "fact_drift"],
            "scope": scope,
        },
    )
    return result["feedback_id"]


def test_pattern_upgrade_replays_all_dependents_and_preserves_active_workflows(
    tmp_path, monkeypatch, workflow_design_review
):
    runtime = WorkflowService(tmp_path)
    monkeypatch.setenv("ROUTINEKEEL_STUDIO_PYTHON", sys.executable)
    old_pattern, new_pattern = executable_pattern(), executable_pattern()
    old_pattern["graph"]["nodes"][0]["capability"] = "text.upper"
    old_pattern["requirements"][0]["capability"] = "text.upper"
    new_pattern["pattern"]["version"] = "1.1.0"
    oldref, newref = "text.preserve@1.0.0", "text.preserve@1.1.0"
    with StudioService.open_workspace(tmp_path) as studio:
        registry = studio.tools.packs
        old = registry.decide(
            decision(
                "approve", registry.register(_pack(tmp_path / "old-pack", old_pattern, "1.0.0"))
            )
        )
        recent = registry.decide(
            decision(
                "approve", registry.register(_pack(tmp_path / "new-pack", new_pattern, "1.1.0"))
            )
        )
        before = qualify(studio, old["ref"], oldref)
        after = qualify(studio, recent["ref"], newref)
        assert before["verdict"] == after["verdict"] == "PASS"
        registry.decide_pattern(
            decision(
                "approve", registry.pattern_status(oldref), evidence=before["qualification_hash"]
            )
        )
        with pytest.raises(StudioError, match="回歸比較"):
            registry.decide_pattern(
                decision(
                    "approve",
                    registry.pattern_status(newref),
                    evidence=after["qualification_hash"],
                )
            )
        legacy = registry.get_asset("pattern", oldref).model
        upgraded = registry.get_asset("pattern", newref).model
    feedback = []
    for workflow in ("weekly_a", "weekly_b"):
        key, _ = _workflow(runtime, workflow, "1.0.0", legacy, old, before)
        state = runtime.resolve_blueprint_capabilities(key)
        built = runtime.install_workflow(
            draft_id=key, resolution={"capability_fingerprint": state["fingerprint"]}
        )
        runtime.install_workflow(
            draft_id=key, build_id=built["build_id"], install_hash=built["install_hash"]
        )
        feedback.append(_feedback(runtime, workflow, tmp_path, "Mixed case", "pattern_defect"))
    config = _feedback(runtime, "weekly_a", tmp_path, "Retain", "configuration", scope="workflow")
    proposal = studio_request(
        tmp_path,
        "propose_pattern_improvement",
        {
            "source_ref": oldref,
            "target_ref": newref,
            "request_id": "propose",
            "feedback_ids": [*feedback, config],
        },
    )
    assert proposal["attribution"]["corroborated"]
    assert len(proposal["dependents"]) == 2
    assert {row["attribution"] for row in proposal["attribution"]["observations"]} == {
        "possible_pattern_defect",
        "workflow_misconfiguration",
    }
    key_by_workflow, negatives = {}, {}
    for workflow in ("weekly_a", "weekly_b"):
        key, negative = _workflow(
            runtime,
            workflow,
            "1.1.0",
            upgraded,
            recent,
            after,
            rehearsal=proposal["proposal_id"],
            bad=workflow == "weekly_b",
            review=workflow_design_review,
        )
        key_by_workflow[f"{workflow}@1.0.0"] = key
        negatives[f"{workflow}@1.0.0"] = [negative]
    request = {
        "proposal_id": proposal["proposal_id"],
        "source_trial": before["qualification_hash"],
        "target_trial": after["qualification_hash"],
        "candidate_drafts": key_by_workflow,
        "negative_cases": negatives,
        "limits": {"cost_micro_usd": 0},
    }
    failed = studio_request(
        tmp_path,
        "compare_pattern_improvement",
        {
            **request,
            "expected_revision": proposal["revision"],
        },
    )
    assert failed["status"] == "regression_failed"
    with StudioService.open_workspace(tmp_path) as studio:
        registry = studio.tools.packs
        pending = registry.pattern_status(newref)
        with pytest.raises(StudioError, match="回歸"):
            registry.decide_pattern(
                decision("approve", pending, evidence=after["qualification_hash"])
            )
    fresh, _ = _workflow(
        runtime,
        "weekly_b",
        "1.1.0",
        upgraded,
        recent,
        after,
        rehearsal=proposal["proposal_id"],
        review=workflow_design_review,
    )
    key_by_workflow["weekly_b@1.0.0"] = fresh
    passed = studio_request(
        tmp_path,
        "compare_pattern_improvement",
        {
            **request,
            "expected_revision": failed["revision"],
        },
    )
    assert passed["status"] == "eligible"
    assert len(passed["comparison_hash"]) == 64
    compare = passed["comparison"]["comparisons"][0]
    with pytest.raises(FlowError, match="僅供試做"):
        runtime.install_workflow(
            draft_id=compare["new_blueprint_hash"],
            build_id=compare["candidate"]["build_id"],
            install_hash=compare["candidate"]["install_hash"],
        )
    with StudioService.open_workspace(tmp_path) as studio:
        registry = studio.tools.packs
        assert (
            registry.decide_pattern(
                decision(
                    "approve",
                    registry.pattern_status(newref),
                    evidence=after["qualification_hash"],
                )
            )["status"]
            == "active"
        )
        assert registry.check_update(oldref) == [newref]
    for workflow in ("weekly_a", "weekly_b"):
        task = runtime.workspace.task(workflow, read_only=True)
        assert task.manifest.version == "1.0.0"
        path = tmp_path / (workflow + ".json")
        path.write_text('{"inputs":{"text":"old version"}}')
        result = runtime.run_workflow_sync({"task": workflow, "source": path})
        assert result["blueprint"]["outputs"] == {"result": "OLD VERSION"}


def test_attribution_does_not_convert_single_or_preference_feedback_into_a_defect():
    row = {
        "pattern_hash": "a" * 64,
        "workflow_ref": "workflow_a@1.0.0",
        "before_hash": "b" * 64,
        "after_hash": "c" * 64,
        "scope": "this_run",
        "failed_checks": [],
        "signs": ["pattern_defect", "fact_drift"],
    }
    result = attribution(
        [
            row,
            {**row, "scope": "preference"},
            {**row, "workflow_ref": "workflow_b@1.0.0", "signs": ["adapter_difference"]},
        ]
    )
    assert not result["corroborated"] and not result["urgent"]
    assert [row["attribution"] for row in result["observations"]] == [
        "possible_pattern_defect",
        "profile_preference",
        "adapter_difference",
    ]
