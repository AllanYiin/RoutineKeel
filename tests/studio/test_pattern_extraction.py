import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest
from flow_base.core.service import WorkflowService
from flow_base.graph.installed import installed_graph
from flow_base.runtime import FlowError
from flow_base.studio import studio_request
from routinekeel_contracts.domain_assets import PatternManifest
from workflow_studio.contracts import StudioError
from workflow_studio.patterns.extractor import (
    ExtractionRequest,
    ExtractionSource,
    export_extraction,
    extract,
    get_extraction,
    save_extraction,
)
from workflow_studio.service import StudioService

TEXT = {"type": "string"}
ROUTE = Path(__file__).resolve().parents[1] / "fixtures/graph_v2/route.json"


def chain():
    def task(name, source, after=None):
        return {
            "kind": "task",
            "id": name,
            "capability": "text.copy",
            "capability_version": "1",
            "inputs": {"source": {"source": source, "port": TEXT}},
            "outputs": {"result": TEXT},
            "permissions": [],
            "side_effect": "none",
            "depends_on": [after] if after else [],
        }

    return {
        "blueprint_schema_version": 2,
        "workflow": {"id": "extract_source", "version": "1.0.0", "goal": "原文", "revision": 1},
        "inputs": {"text": TEXT},
        "nodes": [
            task("first", "input://text"),
            task("middle", "artifact://first/result", "first"),
            task("last", "artifact://middle/result", "middle"),
        ],
        "outputs": {"result": "artifact://last/result"},
        "quality_contract": {
            "golden_cases": [
                {"inputs": {"text": "independent"}, "expected": {"result": "independent"}}
            ]
        },
        "provenance": {},
    }


def evidence(graph):
    return {
        "graph": graph,
        "blueprint_sha256": "f" * 64,
        "wheel_sha256": "e" * 64,
        "dependency_evidence": {
            node["id"]: {"provider": "builtin", "fingerprint": "a" * 64}
            for node in graph["nodes"]
            if node["kind"] == "task"
        },
    }


def request(graph, selected, **updates):
    return {
        "workflow_ref": f"{graph['workflow']['id']}@{graph['workflow']['version']}",
        "expected_revision": 1,
        "selected_nodes": selected,
        "purpose": "保留原文",
        "pattern_id": "text.preserve",
        "request_id": "extraction-case",
        **updates,
    }


def assert_noncontinuous_graph(result):
    pattern = PatternManifest.model_validate(result["pattern"])
    assert [node.id for node in pattern.graph.nodes] == ["first", "last"]
    assert result["report"]["input_mapping"] == {
        "in_1": "input://text",
        "in_2": "artifact://middle/result",
    }
    assert result["report"]["output_mapping"] == {
        "out_1": "artifact://first/result",
        "result": "artifact://last/result",
    }
    assert pattern.boundary.control_inputs["control_in_1"].nodes == ["last"]
    assert pattern.boundary.control_outputs["control_out_1"].nodes == ["first"]
    assert (
        result["report"]["control_bindings"]["control_in_1"]["external_node_hash"]
        == result["report"]["control_bindings"]["control_out_1"]["external_node_hash"]
    )
    assert all(not node.depends_on for node in pattern.graph.nodes)
    assert len(pattern.requirements) == 1
    assert pattern.pattern.trust_status == "candidate"


def test_installed_source_through_existing_studio_bridge(tmp_path, monkeypatch):
    graph = chain()
    raw = tmp_path / "source.zip"
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("design/blueprint.json", json.dumps(graph))
    service = WorkflowService(tmp_path)
    draft = service.install_workflow(archive=raw)
    draft_id = draft["draft_id"]
    binding = service.resolve_blueprint_capabilities(draft_id)
    built = service.install_workflow(
        draft_id=draft_id, resolution={"capability_fingerprint": binding["fingerprint"]}
    )
    service.install_workflow(
        draft_id=draft_id, build_id=built["build_id"], install_hash=built["install_hash"]
    )
    source = tmp_path / "input.json"
    source.write_text('{"inputs":{"text":"source passed"}}')
    result = service.run_workflow_sync({"task": "extract_source", "source": source})
    assert result["blueprint"]["outputs"] == {"result": "source passed"}
    monkeypatch.setenv("ROUTINEKEEL_STUDIO_PYTHON", sys.executable)
    studio = studio_request(tmp_path, "create", {"name": "既有流程", "task": "擷取重用"})
    payload = {
        "project_id": studio["project_id"],
        **request(graph, ["last", "first"]),
        "expected_revision": studio["revision"],
    }
    result = studio_request(tmp_path, "extract_pattern", payload)
    assert_noncontinuous_graph(result)
    assert result["report"]["source_blueprint_hash"] == hashlib.sha256(raw.read_bytes()).hexdigest()
    assert studio_request(tmp_path, "extract_pattern", payload) == result
    with pytest.raises(FlowError, match="安裝的工作流"):
        installed_graph(tmp_path, "extract_source@1.0.1")
    with pytest.raises(FlowError, match="請指定已安裝"):
        studio_request(tmp_path, "extract_pattern", {**payload, "source": evidence(graph)})
    view = {"project_id": studio["project_id"], "extraction_id": result["extraction_id"]}
    assert studio_request(tmp_path, "get_extraction", view) == result
    assert studio_request(tmp_path, "export_extraction", view)["status"] == "draft_only"
    with StudioService.open_workspace(tmp_path) as service:
        assert service.tools.packs.patterns(include_inactive=True) == []


def test_removed_data_absent_from_report_zip_and_logs(tmp_path, capsys):
    graph = chain()
    secrets = [
        "test-private-sentinel-198",
        "C:\\Users\\test\\private\\result.txt",
        "token=private-20261",
        "unapproved source result",
    ]
    graph["nodes"][0]["parameters"] = {
        "api_key": secrets[0],
        "path": secrets[1],
        "note": secrets[2],
        "threshold": 2,
    }
    graph["nodes"][0]["evidence"] = [secrets[1]]
    graph["quality_contract"]["golden_cases"][0]["expected"]["result"] = secrets[3]
    with StudioService.open_workspace(tmp_path) as studio:
        project = studio.projects.create("擷取", {"intent": "保留"})
        result = save_extraction(
            studio,
            project.project_id,
            evidence(graph),
            request(
                graph,
                ["first", "last"],
                portable_parameters={"first": ["api_key", "path", "note", "threshold"]},
            ),
        )
        assert result["report"]["status"] == "draft_only"
        assert result["pattern"]["graph"]["nodes"][0]["parameters"] == {"threshold": 2}
        assert len(result["report"]["removed_artifact_hashes"]) == 3
        assert get_extraction(studio, project.project_id, result["extraction_id"]) == result
        zip_info = export_extraction(studio, tmp_path, project.project_id, result["extraction_id"])
        assert (
            export_extraction(studio, tmp_path, project.project_id, result["extraction_id"])
            == zip_info
        )
        with zipfile.ZipFile(zip_info["path"]) as archive:
            exported = [archive.read(name).decode() for name in archive.namelist()]
            assert archive.namelist() == ["pattern.json", "extraction.json"]
        events = capsys.readouterr()
        for secret in secrets:
            assert all(
                secret not in body
                for body in [
                    events.out,
                    events.err,
                    json.dumps(result),
                    *exported,
                ]
            )


def test_branch_join_review_control_is_preserved_or_explicitly_blocked(tmp_path):
    graph = json.loads(ROUTE.read_bytes())
    origin = ExtractionSource.model_validate(evidence(graph))
    full = extract(
        ExtractionRequest.model_validate(request(graph, ["route", "copy", "manual", "done"])),
        origin,
    )
    assert full["pattern"] is not None
    nodes = {node["id"]: node for node in full["pattern"]["graph"]["nodes"]}
    assert nodes["route"]["routes"] == {"false": ["copy"], "true": ["manual"]}
    assert nodes["manual"]["kind"] == "human_review"
    assert nodes["done"]["branches"]["true"] == {"result": "artifact://manual/result"}
    guarded = extract(ExtractionRequest.model_validate(request(graph, ["copy"])), origin)
    assert guarded["pattern"]["boundary"]["control_inputs"]["control_in_1"] == {
        "kind": "route",
        "nodes": ["copy"],
        "route": "false",
    }
    from flow_base.graph.pattern_trial import run_pattern_trial

    (input_name,) = guarded["pattern"]["boundary"]["inputs"]
    (output_name,) = guarded["pattern"]["boundary"]["outputs"]
    report = run_pattern_trial(guarded["pattern"], [
        {"inputs": {input_name: "branch fixture"}, "expected": {output_name: "branch fixture"}},
    ])
    assert report["verdict"] == "PASS", report
    assert report["results"][0]["control_ports"] == ["input/control_in_1"]
    with StudioService.open_workspace(tmp_path) as studio:
        project = studio.projects.create("結構不完整", {"goal": "提取分支"})
        rejected = save_extraction(
            studio, project.project_id, evidence(graph), request(graph, ["route", "copy", "done"])
        )
        assert rejected["pattern"] is None and rejected["report"]["status"] == "blocked"
        assert any(item["code"] == "PATTERN_GRAPH_INVALID" for item in rejected["report"]["issues"])
        with pytest.raises(StudioError, match="邊界"):
            export_extraction(studio, tmp_path, project.project_id, rejected["extraction_id"])
        with pytest.raises(StudioError, match="不同來源或選區"):
            save_extraction(
                studio, project.project_id, evidence(graph), request(graph, ["route", "manual"])
            )


def test_host_tool_mapping_is_explicit_and_environment_identity_is_hashed():
    graph = chain()
    graph["nodes"][0].update(capability="private-provider.read", capability_version="internal")
    data = evidence(graph)
    data["dependency_evidence"]["first"]["provider"] = "desktop_host"
    origin = ExtractionSource.model_validate(data)
    unresolved = extract(ExtractionRequest.model_validate(request(graph, ["first"])), origin)
    assert unresolved["pattern"]["requirements"][0]["portability"] == "unresolved"
    bound = extract(
        ExtractionRequest.model_validate(
            request(
                graph,
                ["first"],
                capability_map={"first": {"capability": "tabular.read", "version": "1.0.0"}},
            )
        ),
        origin,
    )
    assert bound["pattern"]["requirements"][0]["capability"] == "tabular.read"
    assert bound["report"]["dependencies"][0]["source_locus"] == "host_mediated"
    assert bound["pattern"]["requirements"][0]["execution_loci"] == []
    assert "private-provider.read" not in json.dumps(bound)
