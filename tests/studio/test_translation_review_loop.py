import sys

import pytest
from flow_base.graph.builder import build_graph
from flow_base.graph.capabilities import resolve_graph
from flow_base.graph.external import record_observation
from flow_base.graph.pattern_trial import _fixture_tools
from flow_base.graph.session import GraphSession
from flow_base.graph.source import graph_source
from flow_base.runtime import FlowError, Runtime, atomic_json
from routinekeel_contracts.domain_assets import (
    PatternManifest,
    bind_profile_parameters,
    canonical_json,
)
from workflow_studio.patterns.qualification import qualify
from workflow_studio.patterns.trigger import TaskContext, rank
from workflow_studio.registry.packs import CatalogDecision
from workflow_studio.service import StudioService

from tools.reference_packs import (
    iterative_translation_review_reference,
    translation_review_reference,
    write_reference_pack,
)

LOOP_REF = "text.translation_review_loop@1.0.0"


def test_translation_review_loop_qualifies_with_a_second_critic_round(tmp_path):
    reference, _, records = iterative_translation_review_reference()
    single_pass, _, _ = translation_review_reference()
    assert reference["triggers"]["design"]["conflicts_with"] == [single_pass["pattern"]["id"]]
    assert single_pass["triggers"]["design"]["conflicts_with"] == [reference["pattern"]["id"]]
    archive = write_reference_pack(
        tmp_path / "loop.zip", "translation-review-loop", iterative_translation_review_reference()
    )
    with StudioService.open_workspace(tmp_path / "studio") as studio:
        registry = studio.tools.packs
        item = registry.register(archive)
        registry.decide(
            CatalogDecision(
                "approve",
                item["ref"],
                item["manifest_hash"],
                item["revision"],
                "fixture",
                "isolated test Pack approval",
                "local_terminal",
            )
        )
        report = qualify(studio, item["ref"], LOOP_REF, runtime_python=sys.executable)
        assert report["verdict"] == "PASS" and report["provider_calls"] == 0
        status = registry.pattern_status(LOOP_REF)
        registry.decide_pattern(
            CatalogDecision(
                "approve",
                LOOP_REF,
                status["content_hash"],
                status["revision"],
                "fixture",
                "isolated test Pattern approval",
                "local_terminal",
                report["qualification_hash"],
            )
        )
        context = TaskContext.model_validate(
            {
                "intent": "翻譯修訂後回審",
                "category": "translation",
                "intents": ["translation_iterative_review"],
                "available_inputs": reference["boundary"]["inputs"],
                "facts": ["source_text", "draft_translation", "target_language"],
                "privacy": "host_mediated",
            }
        )
        (recommendation,) = rank(registry, context, {})["recommendations"]
        assert recommendation["ref"] == LOOP_REF and recommendation["missing_inputs"] == []
    assert [case["id"] for case in report["cases"]] == [record[0] for record in records]
    for record, observed in zip(records, report["cases"], strict=True):
        replies = record[-1]
        children = [reply["child_step"] for reply in replies]
        assert children == (
            ["critic", "revise", "critic"] if record[1] == "positive" else ["critic"]
        )
        assert observed["simulated_steps"] == [
            {"step": reply["step"], "kind": "host"} for reply in replies
        ]


def test_translation_review_loop_holds_unapproved_result_at_limit(tmp_path, monkeypatch):
    reference, _, records = iterative_translation_review_reference()
    _, _, _, inputs, expected, replies = records[0]
    body = bind_profile_parameters(PatternManifest.model_validate(reference), {}, {})
    catalog = tmp_path / "host-fixtures.json"
    atomic_json(
        catalog,
        {"version": 1, "expires_at": "2099-01-01T00:00:00+00:00", "tools": _fixture_tools(body)},
    )
    monkeypatch.setenv("ROUTINEKEEL_HOST_CATALOG", str(catalog))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runtime = Runtime(workspace / "share_states")
    graph = {
        **body.model_dump(mode="json", exclude_none=True),
        "blueprint_schema_version": 2,
        "graph_contract_version": "2.2",
        "workflow": {
            "id": "translation_loop",
            "goal": "Review a draft",
            "revision": 1,
            "version": "1.0.0",
        },
        "quality_contract": {
            "golden_cases": [{"inputs": inputs, "expected": expected, "replies": replies}]
        },
        "provenance": {},
    }
    source = graph_source(graph, canonical_json(graph))
    resolved = resolve_graph(source["graph"], workspace)
    plan, _, registry, _ = build_graph(
        source, {"capability_fingerprint": resolved["fingerprint"]}, None, workspace
    )
    view = GraphSession.begin(runtime, plan, {"inputs": inputs}, workspace, registry["bindings"])
    session = GraphSession(runtime, view["session_id"])

    def replay(current, reply):
        pending = current["pending"]
        assert pending["child_step"] == reply["child_step"]
        claim = session.interact(
            "claim", {"revision": current["revision"], "task_id": pending["id"]}, simulation=True
        )
        record_observation(
            workspace,
            pending["idempotency_key"],
            pending["scope_hash"],
            "fixture-only",
            "succeeded",
            reply["outputs"],
        )
        return session.interact(
            "complete",
            {
                "revision": claim["revision"],
                "task_id": pending["id"],
                "outputs": reply["outputs"],
                "evidence": {"actor": "fixture", "receipt": "fixture-only"},
            },
        )

    reject, revise, _ = replies
    view = replay(replay(view, reject), revise)
    assert view["pending"]["inputs"]["value"] == expected["result"]
    view = replay(replay(view, reject), revise)
    assert view["status"] == "waiting" and "outputs" not in view
    resumed = GraphSession(runtime, view["session_id"]).interact("next", {})
    pending = resumed["pending"]
    assert pending["kind"] == "iteration_review" and pending["inputs"]["reason"] == "MAX_ITERATIONS"
    payload = {
        "revision": resumed["revision"],
        "task_id": pending["id"],
        "outputs": revise["outputs"],
    }
    with pytest.raises(FlowError, match="達到上限不能自動通過"):
        session.interact(
            "complete",
            {**payload, "evidence": {"user_text": "fixture reply", "decision": "approved"}},
        )
    completed = session.interact(
        "complete",
        {
            **payload,
            "evidence": {"user_text": "fixture review", "decision": "accept_manual_result"},
        },
    )
    assert completed["status"] == "complete" and completed["outputs"] == expected
