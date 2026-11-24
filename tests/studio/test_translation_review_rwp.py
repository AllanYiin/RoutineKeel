import copy
import sys
from zipfile import ZipFile

import pytest
from flow_base.core.service import WorkflowService
from flow_base.graph.pattern_trial import run_pattern_trial
from routinekeel_contracts.domain_assets import PatternManifest, content_hash
from workflow_studio.contracts import StudioError
from workflow_studio.conversation.facade import dispatch
from workflow_studio.patterns.composer import _node_id, compose
from workflow_studio.patterns.qualification import qualify
from workflow_studio.patterns.trigger import TaskContext, rank
from workflow_studio.registry.packs import CatalogDecision
from workflow_studio.service import StudioService

from tools.reference_packs import (
    iterative_translation_review_reference,
    translation_review_reference,
    write_reference_pack,
)

REF = "text.translation_review@1.0.0"
NAMESPACE = "review_translation"


def trial_cases(records):
    return [
        {
            "inputs": {name: inputs[name] for name in ("source", "target_language")},
            "expected": expected,
            "replies": [
                {"step": "translate", "kind": "host", "outputs": {"result": inputs["draft"]}},
                *[{**reply, "step": _node_id(NAMESPACE, reply["step"])} for reply in replies],
            ],
        }
        for _, _, _, inputs, expected, replies in records
    ]


def proposal_request(proposal):
    return {
        "project_id": proposal["project_id"],
        "expected_revision": proposal["revision"],
        "shown_sections": {
            key: value["sha256"] for key, value in proposal["review_sections"].items()
        },
    }


def test_translation_review_is_recommended_composed_and_replayed(
    tmp_path, monkeypatch, workflow_design_review
):
    monkeypatch.delenv("ROUTINEKEEL_HOST_CATALOG", raising=False)
    reference, capabilities, records = translation_review_reference()
    archive = write_reference_pack(
        tmp_path / "translation-review.zip",
        "translation-review",
        (reference, capabilities, records),
    )
    assert (
        archive.read_bytes()
        == write_reference_pack(
            tmp_path / "recreated.zip", "translation-review", translation_review_reference()
        ).read_bytes()
    )
    assert [node["id"] for node in reference["graph"]["nodes"]] == ["critic", "revise"]
    critic, revise = (_node_id(NAMESPACE, role) for role in ("critic", "revise"))
    cases = trial_cases(records)
    input_ports = {name: {"type": "string"} for name in ("source", "target_language")}
    translator = {
        "id": "translate",
        "kind": "task",
        "capability": "llm.translate",
        "capability_version": "1.0.0",
        "inputs": {
            name: {"source": f"input://{name}", "port": port} for name, port in input_ports.items()
        },
        "outputs": {"result": {"type": "string"}},
        "permissions": [],
        "side_effect": "none",
        "parameters": {
            "agent_role": "translator",
            "context_scope": "task",
            "instructions": "Translate the source into target_language and return a draft as result.",
        },
    }
    with StudioService.open_workspace(tmp_path) as studio:
        registry = studio.tools.packs
        pack = registry.register(archive)
        registry.decide(
            CatalogDecision(
                "approve",
                pack["ref"],
                pack["manifest_hash"],
                pack["revision"],
                "fixture",
                "isolated test Pack approval",
                "local_terminal",
            )
        )
        report = qualify(studio, pack["ref"], REF, runtime_python=sys.executable)
        assert report["verdict"] == "PASS" and report["provider_calls"] == 0
        assert [case["id"] for case in report["cases"]] == [record[0] for record in records]
        pattern = registry.pattern_status(REF)
        registry.decide_pattern(
            CatalogDecision(
                "approve",
                REF,
                pattern["content_hash"],
                pattern["revision"],
                "fixture",
                "isolated test Pattern approval",
                "local_terminal",
                report["qualification_hash"],
            )
        )
        context = {
            "intent": "審查翻譯初稿",
            "category": "translation",
            "intents": ["translation_quality_review"],
            "available_inputs": reference["boundary"]["inputs"],
            "facts": ["source_text", "draft_translation", "target_language"],
            "privacy": "host_mediated",
        }
        excluded = TaskContext.model_validate({**context, "intent": "只做逐字轉錄"})
        assert rank(registry, excluded, {})["recommendations"] == []
        missing_draft = TaskContext.model_validate(
            {
                **context,
                "available_inputs": input_ports,
                "facts": ["source_text", "target_language"],
            }
        )
        assert (
            "draft_translation"
            in rank(registry, missing_draft, {})["recommendations"][0]["missing_inputs"]
        )

        project = studio.interviews.create("翻譯 RWP 試做", "保留原意並檢查漏譯與錯譯")

        def call(action, **params):
            return dispatch(
                studio, tmp_path, action, {"project_id": project["project_id"], **params}
            )

        offer = call(
            "recommend_patterns",
            expected_revision=project["revision"],
            request_id="recommend",
            context=context,
        )
        (suggestion,) = offer["recommendations"]
        assert suggestion["ref"] == REF and suggestion["missing_inputs"] == []
        assert offer["usage"]["graph_reads"] == 0
        accepted = call(
            "decide_pattern",
            expected_revision=offer["revision"],
            request_id="choose",
            recommendation_id=suggestion["recommendation_id"],
            decision="accepted",
            user_text="測試情境採用批評與修訂 Pattern",
        )
        draft = call(
            "prepare_blueprint",
            expected_revision=accepted["revision"],
            request_id="draft",
            graph={
                "blueprint_schema_version": 2,
                "workflow": {
                    "id": "translation_demo",
                    "goal": "忠實翻譯",
                    "revision": 1,
                    "version": "1.0.0",
                },
                "inputs": input_ports,
                "nodes": [translator],
                "outputs": {"result": "artifact://translate/result"},
                "quality_contract": {"success_criteria": ["保留原意、數字及引用"]},
                "provenance": {},
            },
            design={"rules": "翻譯者產出初稿，正式交付譯文來自修訂者"},
        )
        compose = {
            "draft_id": draft["proposal_id"],
            "expected_revision": draft["revision"],
            "request_id": "embed",
            "pattern_ref": REF,
            "decision_id": suggestion["recommendation_id"],
            "insert_location": {"namespace": NAMESPACE},
            "parameter_bindings": {},
            "golden_cases": cases,
            "port_map": {
                "inputs": {
                    "source": "input://source",
                    "draft": "artifact://translate/result",
                    "target_language": "input://target_language",
                },
                "outputs": {"result": ["output://result"]},
            },
        }
        with pytest.raises(StudioError, match="尚未在目前訪談選用"):
            call("compose_pattern", **{**compose, "decision_id": "0" * 64})
        embedded = call("compose_pattern", **compose)
        proposal = embedded["proposal"]
        assert embedded["diff"]["order"] == ["translate", critic, revise]
        assert set(embedded["diff"]["added_nodes"]) == {critic, revise}
        composed = proposal["graph"]
        nodes = {node["id"]: node for node in composed["nodes"]}
        assert nodes[critic]["inputs"]["draft"]["source"] == "artifact://translate/result"
        assert nodes[revise]["inputs"]["issues"]["source"] == f"artifact://{critic}/issues"
        assert composed["outputs"] == {"result": f"artifact://{revise}/result"}
        assert [nodes[step]["parameters"]["agent_role"] for step in (critic, revise)] == [
            "translation_critic",
            "translation_reviser",
        ]
        assert "translate" not in composed["provenance"]["pattern_compositions"][0]["members"]

        review = workflow_design_review(proposal)
        review["tool_locations"] = {step: "host_mediated" for step in nodes}
        final = dispatch(
            studio,
            tmp_path,
            "finalize_workflow_design",
            {
                **proposal_request(proposal),
                "request_id": "finalize",
                "draft_id": proposal["proposal_id"],
                "review": review,
                "draft_reply": "初稿可進入定稿",
            },
        )
        exported = dispatch(
            studio,
            tmp_path,
            "export_blueprint",
            {
                **proposal_request(final),
                "request_id": "export",
                "proposal_id": final["proposal_id"],
                "review_hash": final["review_hash"],
                "decision": "approve",
                "user_text": "確認定稿並匯出",
            },
        )

    service = WorkflowService(tmp_path)
    imported = service.install_workflow(archive=exported["path"])
    readiness = service.resolve_blueprint_capabilities(imported["draft_id"])
    assert readiness["build_status"] == "blocked"
    assert {gap["node"] for gap in readiness["gaps"]} == {"translate", critic, revise}
    assert not service.workspace.discover(read_only=True)
    with ZipFile(exported["path"]) as blueprint:
        assert blueprint.namelist() == ["design/blueprint.json"]

    # The replay runner uses a closed Pattern, so map the composed IDs to ordinary fixture IDs.
    names = {"translate": "translate", critic: "critic", revise: "revise"}
    replay_graph = copy.deepcopy({name: composed[name] for name in ("inputs", "nodes", "outputs")})
    for node in replay_graph["nodes"]:
        node["id"] = names[node["id"]]
        node["depends_on"] = [names[parent] for parent in node["depends_on"]]
        if node["id"] == "revise":
            node["inputs"]["issues"]["source"] = "artifact://critic/issues"
    replay_graph["outputs"]["result"] = "artifact://revise/result"
    replay_cases = copy.deepcopy(cases)
    for case in replay_cases:
        for reply in case["replies"]:
            reply["step"] = names[reply["step"]]
    closed_fixture = {
        **reference,
        "boundary": {"inputs": composed["inputs"], "outputs": reference["boundary"]["outputs"]},
        "graph": replay_graph,
        "requirements": [
            {**reference["requirements"][0], "id": "translate", "capability": "llm.translate"},
            *reference["requirements"],
        ],
    }
    replay = run_pattern_trial(closed_fixture, replay_cases)
    assert replay["verdict"] == "PASS" and replay["provider_calls"] == 0
    assert [case["outputs"] for case in replay["results"]] == [case["expected"] for case in cases]
    assert all(
        [step["step"] for step in case["simulated_host_and_review"]]
        == ["translate", "critic", "revise"]
        for case in replay["results"]
    )


def test_bounded_review_embeds_after_translator_that_supplies_review_state():
    reference, _, _ = iterative_translation_review_reference()
    text = {"type": "string"}
    source = {
        "blueprint_schema_version": 2,
        "workflow": {
            "id": "translate_for_review",
            "goal": "翻譯後回審",
            "revision": 1,
            "version": "1.0.0",
        },
        "inputs": {"source": text, "target_language": text},
        "nodes": [
            {
                "kind": "task",
                "id": "translate",
                "capability": "llm.translate",
                "capability_version": "1.0.0",
                "permissions": [],
                "side_effect": "none",
                "inputs": {
                    "source": {"source": "input://source", "port": text},
                    "target_language": {"source": "input://target_language", "port": text},
                },
                "outputs": {"result": {"type": "object"}},
            },
        ],
        "outputs": {"result": "artifact://translate/result"},
        "quality_contract": {},
        "provenance": {},
    }
    embedded = compose(
        source,
        PatternManifest.model_validate(reference),
        location={"namespace": NAMESPACE},
        mapping={
            "inputs": {"state": "artifact://translate/result"},
            "outputs": {"result": ["output://result"]},
        },
        profiles={},
        parameter_bindings={},
        pack={"ref": "routinekeel.translation-review-loop@1.0.0", "manifest_hash": "a" * 64},
        pattern_hash=content_hash(reference),
        qualification_hash="b" * 64,
        decision_id="c" * 64,
    )
    translation, loop = embedded["graph"]["nodes"]
    assert translation["id"] == "translate"
    assert loop["kind"] == "repeat_until" and loop["max_iterations"] == 2
    assert loop["on_limit"] == "review_required"
    assert loop["initial"]["source"] == "artifact://translate/result"
    assert embedded["graph"]["outputs"]["result"] == f"artifact://{loop['id']}/value"
    members = embedded["graph"]["provenance"]["pattern_compositions"][0]["requirements"]
    assert set(members) == {f"{loop['id']}/critic", f"{loop['id']}/revise"}
