import copy
import json
import sys
import zipfile

import pytest
from flow_base.core.service import WorkflowService
from flow_base.graph.pattern_trial import run_pattern_trial
from flow_base.graph.qualification import BindingReview
from flow_base.runtime import FlowError
from flow_base.studio import studio_request
from routinekeel_contracts.domain_assets import PatternManifest, content_hash
from routinekeel_contracts.graph_validator import validate_blueprint
from test_pattern_extraction import chain, evidence, request
from test_pattern_registry import decision
from test_pattern_trigger import TEXT, activate, candidates, text_task
from workflow_studio.contracts import StudioError
from workflow_studio.patterns.composer import compose
from workflow_studio.patterns.extractor import (
    ExtractionRequest,
    ExtractionSource,
    extract,
)
from workflow_studio.service import StudioService


def package(service, path, graph=None, *, install=False):
    if graph is not None:
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("design/blueprint.json", json.dumps(graph))
    draft = service.install_workflow(archive=path)
    key = draft["draft_id"]
    resolution = service.resolve_blueprint_capabilities(key)
    if resolution.get("portable_decisions"):
        selections = {
            row["node"]: row["candidates"][0]["adapter"] for row in resolution["portable_decisions"]
        }
        preview = service.preview_binding_qualification(key, selections)
        assert (
            service.qualify_blueprint_bindings(
                key,
                selections,
                BindingReview(preview["request_hash"], "simulated local Stage 24 approval"),
            )["status"]
            == "qualified"
        )
        resolution = service.resolve_blueprint_capabilities(key)
    built = service.install_workflow(
        draft_id=key, resolution={"capability_fingerprint": resolution["fingerprint"]}
    )
    if install:
        service.install_workflow(
            draft_id=key, build_id=built["build_id"], install_hash=built["install_hash"]
        )
    return built


def executable_pattern(*, gate=False):
    pattern, _ = candidates()
    task = pattern["graph"]["nodes"][0]
    task["capability_version"] = "1"
    pattern["requirements"][0]["version"] = None
    if gate:
        task["capability"] = "text.upper"
        pattern["requirements"][0]["capability"] = "text.upper"
        pattern["triggers"]["design"]["gate"] = "fidelity"
        pattern["quality_contract"]["delivery_gate"] = {"port": "text", "equals": "MATCH"}
    return pattern


def options(pattern, *, mapping=None, after=None, before=None, namespace="insert"):
    return {
        "location": {"namespace": namespace, "after": after or [], "before": before or []},
        "mapping": mapping
        or {
            "inputs": {"text": "artifact://first/result"},
            "outputs": {"text": ["node://middle/source"]},
        },
        "profiles": {},
        "parameter_bindings": {},
        "pack": {"ref": "routinekeel.text@1.0.0", "manifest_hash": "a" * 64},
        "pattern_hash": content_hash(pattern),
        "qualification_hash": "b" * 64,
        "decision_id": "c" * 64,
    }


def test_composition_is_revisioned_and_verifies_in_existing_builder(
    tmp_path, monkeypatch, workflow_design_review
):
    runtime = WorkflowService(tmp_path)
    original = chain()
    package(runtime, tmp_path / "old.zip", original, install=True)
    with StudioService.open_workspace(tmp_path) as studio:
        activate(studio, tmp_path / "patterns.zip", [executable_pattern()])
    monkeypatch.setenv("ROUTINEKEEL_STUDIO_PYTHON", sys.executable)
    project = studio_request(tmp_path, "create", {"name": "合成", "task": "保留原文"})
    pid = project["project_id"]
    advice = studio_request(
        tmp_path,
        "recommend_patterns",
        {
            "project_id": pid,
            "expected_revision": project["revision"],
            "request_id": "advice",
            "context": text_task(),
        },
    )
    (recommendation,) = advice["recommendations"]
    choice = studio_request(
        tmp_path,
        "decide_pattern",
        {
            "project_id": pid,
            "expected_revision": advice["revision"],
            "request_id": "accept",
            "recommendation_id": recommendation["recommendation_id"],
            "decision": "accepted",
            "user_text": "採用保真 Pattern",
        },
    )
    design = copy.deepcopy(original)
    design["workflow"]["version"] = "2.0.0"
    design["quality_contract"]["success_criteria"] = ["完整保留輸入文字"]
    design["quality_contract"]["adapter_cases"] = {
        "copier": [
            {"id": "first", "inputs": {"source": "one"}, "expected": {"result": "one"}},
            {"id": "second", "inputs": {"source": "two"}, "expected": {"result": "two"}},
        ]
    }
    proposal = studio_request(
        tmp_path,
        "prepare_blueprint",
        {
            "project_id": pid,
            "expected_revision": choice["revision"],
            "request_id": "draft",
            "graph": design,
            "design": {"rules": "完整保留來源文字"},
        },
    )
    parameters = {
        "project_id": pid,
        "draft_id": proposal["proposal_id"],
        "expected_revision": proposal["revision"],
        "request_id": "compose",
        "pattern_ref": TEXT,
        "decision_id": recommendation["recommendation_id"],
        "insert_location": {"namespace": "copy_checked"},
        "port_map": {
            "inputs": {"text": "artifact://first/result"},
            "outputs": {"text": ["node://middle/source"]},
        },
        "parameter_bindings": {},
    }
    with pytest.raises(FlowError):
        studio_request(
            tmp_path,
            "compose_pattern",
            {
                **parameters,
                "port_map": {"inputs": {"text": "input://absent"}, "outputs": {}},
            },
        )
    assert studio_request(tmp_path, "get", {"project_id": pid})["revision"] == proposal["revision"]
    result = studio_request(tmp_path, "compose_pattern", parameters)
    assert studio_request(tmp_path, "compose_pattern", parameters) == result
    (added,) = result["diff"]["added_nodes"]
    assert added.startswith("rwp_copy_checked_")
    assert result["diff"]["changed_outputs"] == ["node://middle/source"]
    composed, order = validate_blueprint(result["proposal"]["graph"])
    assert order == ["first", added, "middle", "last"]
    assert composed.provenance["pattern_compositions"][0]["ref"] == TEXT
    final = studio_request(
        tmp_path,
        "finalize_workflow_design",
        {
            "project_id": pid,
            "expected_revision": result["revision"],
            "request_id": "finalize",
            "draft_id": result["proposal_id"],
            "review": workflow_design_review(result["proposal"]),
            "draft_reply": "初稿可進入定稿",
            "shown_sections": {key: item["sha256"] for key, item in result["proposal"]["review_sections"].items()},
        },
    )
    frozen = studio_request(
        tmp_path,
        "export_blueprint",
        {
            "project_id": pid,
            "expected_revision": final["revision"],
            "request_id": "freeze",
            "proposal_id": final["proposal_id"],
            "review_hash": final["review_hash"],
            "shown_sections": {key: item["sha256"] for key, item in final["review_sections"].items()},
            "decision": "approve",
            "user_text": "確認匯出新版",
        },
    )
    candidate = package(runtime, frozen["path"])
    assert candidate["validation"]["status"] == "PASS"
    key = runtime.install_workflow(archive=frozen["path"])["draft_id"]
    prior_resolution = runtime.resolve_blueprint_capabilities(key)
    with StudioService.open_workspace(tmp_path) as studio:
        registry = studio.tools.packs
        pack = registry.get(composed.provenance["pattern_compositions"][0]["pack_ref"])
        registry.decide(decision("disable", pack))
    blocked = runtime.resolve_blueprint_capabilities(key)
    assert blocked["build_status"] == "blocked"
    assert any(
        row["code"] == "PATTERN_APPROVAL_REQUIRED" for row in blocked["build_gaps"]
    )
    with pytest.raises(FlowError):
        runtime.install_workflow(
            draft_id=key,
            resolution={"capability_fingerprint": prior_resolution["fingerprint"]},
        )
    with pytest.raises(FlowError, match="停用"):
        runtime.install_workflow(
            draft_id=key,
            build_id=candidate["build_id"],
            install_hash=candidate["install_hash"],
        )
    assert (
        runtime.workspace.task("extract_source", read_only=True).manifest.version
        == "1.0.0"
    )
    source = tmp_path / "run-original.json"
    source.write_text('{"inputs":{"text":"prior revision"}}')
    completed = runtime.run_workflow_sync({"task": "extract_source", "source": source})
    assert completed["blueprint"]["outputs"] == {"result": "prior revision"}


def test_quality_gate_is_checked_on_success_paths_and_in_runtime(tmp_path, monkeypatch):
    # This Graph fixture uses synthetic catalog hashes. Real admission is tested above.
    monkeypatch.setattr(
        "flow_base.graph.builder.verify_pattern_admission", lambda *args: None
    )
    original = chain()
    raw = executable_pattern(gate=True)
    raw["quality_contract"]["invariants"] = [
        {
            "id": "fidelity_status",
            "version": "1",
            "output": "text",
            "evaluator": "equals",
            "expected": "MATCH",
        }
    ]
    pattern = PatternManifest.model_validate(raw)
    output = compose(original, pattern, **options(raw))
    graph = output["graph"]
    record = graph["provenance"]["pattern_compositions"][0]
    assert record["gate"]["deliveries"] == ["result"]
    graph["quality_contract"]["golden_cases"] = [
        {
            "inputs": {"text": "match"},
            "expected": {"result": "MATCH"},
            "expected_result_status": "accepted",
        },
        {
            "inputs": {"text": "wrong"},
            "expected": {"result": "WRONG"},
            "expected_result_status": "rejected",
        },
    ]
    graph["quality_contract"]["adapter_cases"] = {
        "copier": [
            {"id": "first", "inputs": {"source": "match"}, "expected": {"result": "MATCH"}},
            {"id": "second", "inputs": {"source": "wrong"}, "expected": {"result": "WRONG"}},
        ]
    }
    service = WorkflowService(tmp_path)
    assert package(service, tmp_path / "gated.zip", graph, install=True)["status"] == "verified"
    request_path = tmp_path / "wrong.json"
    request_path.write_text('{"inputs":{"text":"wrong"}}')
    rejected = service.run_workflow_sync({"task": "extract_source", "source": request_path})
    assert rejected["graph_result_status"] == "rejected"
    assert rejected["graph_delivery_status"] == "withheld"
    stored = service.workflow_quality(rejected["blueprint"]["session_id"])
    assert any(
        item["id"].endswith("/fidelity_status") and item["status"] == "fail"
        for item in stored["checks"]
    )
    tampered = copy.deepcopy(graph)
    tampered["provenance"].clear()
    with pytest.raises(StudioError, match="缺少合成證據"):
        validate_blueprint(tampered)
    with pytest.raises(StudioError, match="循環"):
        compose(original, pattern, **options(raw, before=["first"]))


def test_all_done_fallback_is_not_a_successful_gate_path():
    original = chain()
    original["graph_contract_version"] = "2.2"
    original["nodes"][-1] = {
        "kind": "join",
        "id": "last",
        "policy": "all_done",
        "depends_on": ["middle"],
        "inputs": {"result": {"source": "artifact://middle/result", "port": {"type": "string"}}},
        "outputs": {"result": {"type": "string"}},
        "fallbacks": {"result": "input://text"},
    }
    raw = executable_pattern(gate=True)
    with pytest.raises(StudioError, match="繞過"):
        compose(original, PatternManifest.model_validate(raw), **options(raw))


def test_noncontiguous_pattern_preserves_control_and_data_boundary_when_reinserted():
    original = chain()
    source = ExtractionSource.model_validate(evidence(original))
    draft = extract(ExtractionRequest.model_validate(request(original, ["first", "last"])), source)
    pattern = PatternManifest.model_validate(draft["pattern"])
    result = run_pattern_trial(
        draft["pattern"],
        [
            {
                "inputs": {"in_1": "first", "in_2": "last"},
                "expected": {"out_1": "first", "result": "last"},
            },
        ],
    )
    assert result["verdict"] == "PASS", result
    assert result["results"][0]["control_ports"] == [
        "input/control_in_1",
        "output/control_out_1",
    ]
    middle = copy.deepcopy(original)
    middle["nodes"] = [original["nodes"][1]]
    middle["nodes"][0]["depends_on"] = []
    middle["nodes"][0]["inputs"]["source"]["source"] = "input://text"
    middle["outputs"] = {"result": "artifact://middle/result"}
    out = compose(
        middle,
        pattern,
        **options(
            draft["pattern"],
            mapping={
                "inputs": {"in_1": "input://text", "in_2": "artifact://middle/result"},
                "outputs": {"out_1": ["node://middle/source"], "result": ["output://result"]},
                "control_inputs": {"control_in_1": "middle"},
                "control_outputs": {"control_out_1": ["middle"]},
            },
        ),
    )
    model, order = validate_blueprint(out["graph"])
    assert order[1] == "middle" and len(order) == 3
    nodes = {node.id: node for node in model.nodes}
    assert nodes["middle"].depends_on == [order[0]]
    assert nodes[order[2]].depends_on == ["middle"]


def test_second_pattern_cannot_replace_a_confirmed_internal_node():
    raw = executable_pattern()
    base = compose(chain(), PatternManifest.model_validate(raw), **options(raw))["graph"]
    (first,) = base["provenance"]["pattern_compositions"][0]["members"]
    second = copy.deepcopy(raw)
    second["pattern"]["id"] = "text.second"
    with pytest.raises(StudioError, match="內容或連線"):
        compose(
            base,
            PatternManifest.model_validate(second),
            **options(
                second,
                namespace="second",
                mapping={
                    "inputs": {"text": "artifact://first/result"},
                    "outputs": {"text": [f"node://{first}/source"]},
                },
            ),
        )


def test_multiple_patterns_and_bounded_nested_graph_use_the_same_validator():
    raw = executable_pattern()
    base = chain()
    first = compose(base, PatternManifest.model_validate(raw), **options(raw))["graph"]
    second = copy.deepcopy(raw)
    second["pattern"]["id"] = "text.later"
    result = compose(
        first,
        PatternManifest.model_validate(second),
        **options(
            second,
            namespace="second",
            mapping={
                "inputs": {"text": "artifact://middle/result"},
                "outputs": {"text": ["node://last/source"]},
            },
        ),
    )
    assert len(result["graph"]["provenance"]["pattern_compositions"]) == 2
    nested = copy.deepcopy(raw)
    nested["pattern"]["id"] = "text.nested"
    port = {"type": "array", "items": {"type": "string"}}
    child = nested["graph"]["nodes"][0]
    child["inputs"]["source"]["source"] = "input://item"
    nested["graph"] = {
        "inputs": {"notes": port},
        "nodes": [
            {
                "id": "batch",
                "kind": "foreach",
                "items": "input://notes",
                "max_items": 3,
                "on_item_failure": "failed",
                "limits": {
                    "timeout_seconds": 20,
                    "max_work": 3,
                    "max_model_calls": 0,
                    "max_cost_micro_usd": 0,
                },
                "body": {
                    "inputs": {"item": {"type": "string"}},
                    "nodes": [child],
                    "outputs": {"result": "artifact://copy/result"},
                },
                "outputs": {"results": port},
            }
        ],
        "outputs": {"notes": "artifact://batch/results"},
    }
    nested["boundary"]["inputs"] = nested["graph"]["inputs"]
    nested["boundary"]["outputs"] = {"notes": port}
    base["inputs"]["notes"] = port
    mapping = {"inputs": {"notes": "input://notes"}, "outputs": {"notes": ["output://notes"]}}
    merged = compose(
        base, PatternManifest.model_validate(nested), **options(nested, mapping=mapping)
    )
    model, order = validate_blueprint(merged["graph"])
    (record,) = model.provenance["pattern_compositions"]
    (batch,) = record["members"]
    assert batch in order
    assert record["requirements"][batch + "/copy"]["capability"] == "text.copy"
    assert next(node for node in model.nodes if node.id == batch).max_items == 3
