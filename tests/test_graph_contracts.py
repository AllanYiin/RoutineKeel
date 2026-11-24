import hashlib
import json
import zipfile
from importlib.resources import files
from pathlib import Path

import pytest
from flow_base.core.service import WorkflowService
from flow_base.graph.source import inspect_graph
from flow_base.runtime import FlowError
from jsonschema import Draft202012Validator
from routinekeel_contracts.graph import Blueprint
from test_blueprint_install import blueprint, write_source


def example():
    return json.loads((Path(__file__).parent / "fixtures/graph_v2/route.json").read_bytes())


def test_schema_order_and_import_in_another_session(tmp_path):
    payload = example()
    schema = json.loads(
        files("routinekeel_contracts").joinpath("blueprint_v2.schema.json").read_bytes()
    )
    assert schema == Blueprint.model_json_schema()
    Draft202012Validator(schema).validate(payload)
    canonical, order = inspect_graph(payload)
    assert order == ["route", "copy", "manual", "done"]
    payload["nodes"].reverse()
    assert inspect_graph(payload) == (canonical, order)
    path = tmp_path / "graph.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("design/blueprint.json", json.dumps(canonical))
    draft = WorkflowService(tmp_path).install_workflow(archive=path)
    stored = WorkflowService(tmp_path).install_workflow(draft_id=draft["draft_id"])
    assert stored["source"]["graph"] == canonical
    assert stored["builds"] == []
    assert not WorkflowService(tmp_path).workspace.discover(read_only=True)


@pytest.mark.parametrize(
    "fault",
    ["cycle", "missing", "type", "orphan", "branch", "duplicate", "predicate", "op", "unknown"],
)
def test_invalid_graph_cannot_be_admitted(fault):
    payload = example()
    join, review, task, switch = payload["nodes"]
    if fault == "cycle":
        switch["depends_on"] = ["done"]
    elif fault == "missing":
        task["inputs"]["message"]["source"] = "artifact://absent/result"
    elif fault == "type":
        review["outputs"]["result"]["type"] = "integer"
    elif fault == "orphan":
        payload["nodes"].append({**task, "id": "unused"})
    elif fault == "branch":
        join["branches"]["true"]["result"] = "artifact://copy/result"
    elif fault == "duplicate":
        review["id"] = task["id"]
    elif fault == "predicate":
        switch["condition"]["value"] = "true"
    elif fault == "op":
        switch["condition"]["op"] = "python"
    else:
        task["kind"] = "unbounded"
    with pytest.raises(FlowError) as caught:
        inspect_graph(payload)
    assert caught.value.code == ("GRAPH_UNSUPPORTED" if fault == "unknown" else "GRAPH_INVALID")


@pytest.mark.parametrize("kind", ["foreach", "repeat_until"])
def test_bounded_subgraph_types_and_limits(kind):
    payload = example()
    boolean = {"type": "boolean"}
    name = "item" if kind == "foreach" else "value"
    result = "result" if kind == "foreach" else "value"
    inner = {
        **payload["nodes"][2],
        "id": "work",
        "inputs": {"message": {"source": "input://" + name, "port": boolean}},
        "outputs": {result: boolean},
    }
    body = {
        "nodes": [inner],
        "inputs": {name: boolean},
        "outputs": {result: "artifact://work/" + result},
    }
    node = {
        "kind": kind,
        "id": "batch",
        "body": body,
        "limits": {
            "timeout_seconds": 20,
            "max_work": 4,
            "max_model_calls": 0,
            "max_cost_micro_usd": 0,
        },
    }
    if kind == "foreach":
        payload["inputs"] = {"rows": {"type": "array", "items": boolean}}
        node.update(
            items="input://rows",
            max_items=3,
            on_item_failure="failed",
            outputs={"results": {"type": "array", "items": boolean}},
        )
        payload["outputs"] = {"rows": "artifact://batch/results"}
        maximum = "max_items"
    else:
        node.update(
            initial={"source": "input://needs_review", "port": boolean},
            max_iterations=3,
            on_limit="review_required",
            condition={"source": "artifact://work/value", "op": "equals", "value": True},
            outputs={"value": boolean},
        )
        payload["outputs"] = {"ready": "artifact://batch/value"}
        maximum = "max_iterations"
    payload["nodes"] = [node]
    assert inspect_graph(payload)[1] == ["batch"]
    node[maximum] = 0
    with pytest.raises(FlowError):
        inspect_graph(payload)


def test_v1_import_retains_source_schema_and_manual_review(tmp_path):
    service = WorkflowService(tmp_path)
    path = blueprint(tmp_path / "legacy.zip")
    original = path.read_bytes()
    draft = service.install_workflow(archive=path)
    source = service.install_workflow(draft_id=draft["draft_id"])["source"]
    graph = source["graph"]
    assert graph["provenance"]["source_sha256"] == hashlib.sha256(original).hexdigest()
    assert graph["quality_contract"]["v1_artifact_contracts"] == source["contracts"]
    assert graph["inputs"][graph["provenance"]["port_names"]["text"]]["type"] == "string"
    assert graph["provenance"]["review_points"] == source["definition"]["review"]["points"]
    assert source["graph_order"] == ["step_1", "review_1", "step_2"]
    assert path.read_bytes() == original


def test_maximum_linear_v1_preserves_every_step_review_and_named_output(tmp_path):
    steps = [
        {"id": str(i), "title": f"步驟 {i}", "inputs": [f"p{i - 1}"], "outputs": [f"p{i}"]}
        for i in range(1, 201)
    ]
    points = [{"after_step": s["id"], "shows": s["outputs"]} for s in steps]
    points.append({"after_step": "1", "is_step": "2"})
    definition = {
        "definition_version": "0.2",
        "workflow_id": "max_flow",
        "execution": {"order": "linear"},
        "steps": steps,
        "review": {"points": points},
        "final_outputs": {"items": [{"name": "p200", "from": "200"}]},
    }
    manifest = {
        "schema_version": 1,
        "runtime_api": "0.2",
        "id": "max_flow",
        "title": "最大舊藍圖",
        "version": "1.0.0",
    }
    path = write_source(tmp_path / "max.zip", manifest, definition)
    service = WorkflowService(tmp_path)
    draft = service.install_workflow(archive=path)
    source = service.install_workflow(draft_id=draft["draft_id"])["source"]
    graph = source["graph"]
    assert graph["graph_contract_version"] == "2.1" and len(graph["nodes"]) == 401
    by_id = {node["id"]: node for node in graph["nodes"]}
    assert by_id["step_2"]["kind"] == "human_review"
    assert by_id["review_1"]["inputs"] and by_id["step_2"]["depends_on"] == ["review_1"]
    assert source["graph_order"][-2:] == ["review_200", "delivery"]
    with pytest.raises(FlowError):
        inspect_graph({**graph, "graph_contract_version": "2.0"})


@pytest.mark.parametrize("policy", ["all_done", "all_success"])
def test_order_barriers_can_join_skipped_arms_without_reading_their_outputs(policy):
    payload = example()
    nodes = payload["nodes"]
    nodes[0] = {"kind": "join", "id": "barrier", "policy": policy, "depends_on": ["manual", "copy"]}
    nodes.append({**nodes[2], "id": "after", "depends_on": ["barrier"]})
    payload["outputs"] = {"result": "artifact://after/result"}
    assert inspect_graph(payload)[1][-2:] == ["barrier", "after"]
    if policy == "all_success":
        nodes[0].update(
            inputs={"result": {"source": "artifact://manual/result", "port": {"type": "string"}}},
            outputs={"result": {"type": "string"}},
        )
        with pytest.raises(FlowError):
            inspect_graph(payload)
