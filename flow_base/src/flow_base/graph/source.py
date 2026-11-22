import hashlib

from routinekeel_contracts.errors import StudioError
from routinekeel_contracts.graph_validator import validate_blueprint

from ..blueprints.source import review_roles
from ..runtime import FlowError


def inspect_graph(payload):
    try:
        graph, order = validate_blueprint(payload)
    except StudioError as exc:
        raise FlowError(exc.code, str(exc)) from exc
    return graph.model_dump(mode="json", exclude_none=True), order


def _legacy_port(schema):
    if not isinstance(schema, dict) or not isinstance(schema.get("type"), str):
        return {"type": "json"}
    port = {"type": schema["type"]}
    if isinstance(schema.get("format"), str):
        port["format"] = schema["format"]
    if port["type"] == "array" and "items" in schema:
        port["items"] = _legacy_port(schema["items"])
    return port


def convert_linear(source):
    """Represent ordering and handoffs without guessing an unresolved capability."""
    artifacts = source["contracts"]
    names = dict.fromkeys([*source["inputs"], *artifacts])
    ports = {name: f"p{i}" for i, name in enumerate(names, 1)}
    types = {name: _legacy_port(artifacts.get(name, {}).get("schema")) for name in names}
    refs = {name: "input://" + ports[name] for name in source["inputs"]}
    nodes = []
    previous = []
    reviews = source["definition"]["review"]["points"]
    after, human = review_roles(reviews)
    for i, step in enumerate(source["definition"]["steps"], 1):
        key = f"step_{i}"
        node = {
            "id": key,
            "depends_on": previous,
            "inputs": {ports[n]: {"source": refs[n], "port": types[n]} for n in step["inputs"]},
            "outputs": {ports[n]: types[n] for n in step["outputs"]},
            "evidence": [f"workflow.definition.json#/steps/{i - 1}"],
        }
        if step.get("actor") == "human" or step["id"] in human:
            node.update(kind="human_review", prompt=step["title"])
        else:
            node.update(
                kind="task",
                capability="legacy.resolve",
                capability_version="1",
                side_effect="unresolved",
                permissions=[],
                parameters={"source_step_id": step["id"]},
            )
        nodes.append(node)
        refs.update({n: f"artifact://{key}/{ports[n]}" for n in step["outputs"]})
        previous = [key]
        for j, review in enumerate(reviews):
            if review is after.get(step["id"]):
                gate = f"review_{j + 1}"
                nodes.append(
                    {
                        "kind": "human_review",
                        "id": gate,
                        "depends_on": previous,
                        "prompt": str(review.get("title") or "請確認本步驟成果"),
                        "inputs": {
                            ports[n]: {"source": refs[n], "port": types[n]}
                            for n in review.get("shows", [])
                            if n in refs
                        },
                        "evidence": [f"workflow.definition.json#/review/points/{j}"],
                    }
                )
                previous = [gate]
    finals = [v["name"] for v in source["definition"]["final_outputs"]["items"]]
    nodes.append(
        {
            "kind": "join",
            "id": "delivery",
            "policy": "all_success",
            "depends_on": previous,
            "inputs": {ports[n]: {"source": refs[n], "port": types[n]} for n in finals},
            "outputs": {ports[n]: types[n] for n in finals},
        }
    )
    manifest = source["manifest"]
    graph = {
        "blueprint_schema_version": 2,
        "graph_contract_version": "2.1",
        "workflow": {
            "id": manifest["id"],
            "goal": manifest["title"],
            "version": manifest["version"],
            "revision": 1,
        },
        "inputs": {ports[n]: types[n] for n in source["inputs"]},
        "nodes": nodes,
        "outputs": {ports[n]: "artifact://delivery/" + ports[n] for n in finals},
        "quality_contract": {"v1_artifact_contracts": artifacts},
        "provenance": {
            "source_sha256": source["source_hash"],
            "port_names": ports,
            "source_format": 1,
            "review_points": reviews,
        },
    }
    return inspect_graph(graph)


def graph_source(payload, raw):
    graph, order = inspect_graph(payload)
    workflow = graph["workflow"]
    return {
        "source_hash": hashlib.sha256(raw).hexdigest(),
        "blueprint_schema_version": 2,
        "graph": graph,
        "steps": order,
        "manifest": {
            "id": workflow["id"],
            "version": workflow["version"],
            "title": workflow["goal"],
        },
        "definition": {
            "review": {"points": [n["id"] for n in graph["nodes"] if n["kind"] == "human_review"]}
        },
        "dependencies": {},
        "gaps": {},
        "findings": [],
    }
