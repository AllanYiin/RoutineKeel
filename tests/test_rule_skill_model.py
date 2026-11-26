import io
import json
import os
import zipfile
from hashlib import sha256
from urllib.request import Request, urlopen

import pytest
from flow_base.blueprints.source import inspect_blueprint
from flow_base.graph.builder import build_graph
from flow_base.graph.capabilities import resolve_graph
from flow_base.graph.external import record_observation
from flow_base.graph.session import GraphSession
from flow_base.runtime import digest, read_json
from flow_base.workspace import Workspace
from routinekeel_contracts.base_steps import JUDGE, ModelReview, task_node
from routinekeel_contracts.domain_assets import content_hash
from routinekeel_contracts.graph_validator import validate_blueprint
from test_base_steps import graph_with

RUBRIC = {"required_count": 12}
INPUTS = {"candidate": "Inventory showed 12 items.", "rubric": RUBRIC}
EXPECTED = {"verdict": {"passed": True, "rationale": "stock-12-approved", "findings": []}}
RULE = """# Inventory certification
Compare the inventory count with the rubric's required_count.
When the inventory count is correct, return passed=true, findings=[], and
set rationale to the exact string stock-12-approved.
"""


def _claim(tmp_path, catalog_path, rule):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("SKILL.md", rule)
    attachments = {
        "skills/inventory-certification/SKILL.md": rule.encode(),
        "skills/inventory-certification/bundle.zip": archive.getvalue(),
    }
    documents = {"inventory-certification": {"SKILL.md": rule}}
    node = task_node(JUDGE, "judge", {"candidate": "input://candidate", "rubric": "input://rubric"})
    graph = graph_with({"candidate": "json", "rubric": "object"}, [node], {"verdict": "artifact://judge/verdict"},
                       INPUTS, EXPECTED)
    graph["provenance"]["skill_files"] = {name: sha256(data).hexdigest() for name, data in attachments.items()}
    policy = {"mode": "stop_on_error", "scope": "Fixture inventory review", "critical_steps": {}}
    graph["quality_contract"]["execution_review"] = policy
    checked, _ = validate_blueprint(graph)
    canonical = checked.model_dump(mode="json", exclude_none=True)
    details = {
        "policy": policy,
        "skills": {"inventory-certification": ["judge"]},
        "prefilled_decisions": {
            "quality": {key: value for key, value in graph["quality_contract"].items() if key != "execution_review"},
            "goal": checked.workflow.goal, "evolution": {"automatic_promotion": False}, "steps": canonical["nodes"],
            "inputs": canonical["inputs"], "outputs": canonical["outputs"],
        },
    }
    graph["provenance"]["design_review"] = {"details": details, "sha256": content_hash(details)}
    source = io.BytesIO()
    with zipfile.ZipFile(source, "w") as bundle:
        bundle.writestr("graph/blueprint.json", json.dumps(graph))
        for name, data in attachments.items():
            bundle.writestr(f"graph/{name}", data)
    catalog = read_json(catalog_path)
    catalog["tools"][JUDGE] = {
        **catalog["tools"]["test-sink"], "tool": JUDGE, "destination": "local-ollama",
        "permissions": [], "side_effect": "none",
        "inputs": {key: binding["port"] for key, binding in node["inputs"].items()},
        "outputs": node["outputs"], "resources": {"model_calls": 1, "cost_micro_usd": 0},
    }
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    workspace = Workspace(tmp_path)
    source, files = inspect_blueprint(source.getvalue())
    resolution = resolve_graph(source["graph"], workspace.root)
    plan, _, registry, _ = build_graph(
        source, {"capability_fingerprint": resolution["fingerprint"]}, None, workspace.root,
        attachments=files,
    )
    runtime, bindings = workspace.runtime, registry["bindings"]
    view = GraphSession.begin(runtime, plan, {"inputs": INPUTS}, tmp_path, bindings)
    session = GraphSession(runtime, view["session_id"])
    task = view["pending"]
    assert task["scope"]["skill_documents_hash"] == digest(documents)
    claimed = session.interact("claim", {"revision": view["revision"], "task_id": task["id"]})
    assert claimed["dispatch"]["skill_documents"] == documents
    return session, task, claimed


def test_rule_only_skill_text_is_bound_into_the_dispatch_scope(tmp_path, desktop_test_catalog):
    _, task, claimed = _claim(tmp_path, desktop_test_catalog, RULE)
    _, changed, _ = _claim(tmp_path, desktop_test_catalog, RULE.replace("stock-12-approved", "changed"))
    assert task["scope"]["skill_documents_hash"] != changed["scope"]["skill_documents_hash"]
    assert claimed["dispatch"]["scope_hash"] == task["scope_hash"]


@pytest.mark.skipif(not os.environ.get("ROUTINEKEEL_TEST_OLLAMA_MODEL"), reason="local model integration opt-in")
def test_rule_only_skill_is_used_by_a_real_local_model(tmp_path, desktop_test_catalog):
    session, task, claimed = _claim(tmp_path, desktop_test_catalog, RULE)
    dispatch = claimed["dispatch"]
    uri = "http://127.0.0.1:11434"
    model = os.environ["ROUTINEKEEL_TEST_OLLAMA_MODEL"]
    assert ":" in model and not model.endswith(":cloud")
    with urlopen(uri + "/api/tags", timeout=10) as response:
        identity = next(item["digest"] for item in json.load(response)["models"] if item["name"] == model)
    request = {
        "model": model, "stream": True, "think": False,
        "format": {"type": "object", "properties": {"verdict": ModelReview.model_json_schema()},
                   "required": ["verdict"], "additionalProperties": False},
        "options": {"temperature": 0, "num_gpu": 0, "num_predict": 160},
        "messages": [
            {"role": "system", "content": dispatch["instructions"] + ' Return only JSON {"verdict": ...}.'},
            {"role": "user", "content": json.dumps({
                "skill_rules": dispatch["skill_documents"], "inputs": dispatch["arguments"],
            })},
        ],
    }
    content = []
    view = claimed
    with urlopen(Request(uri + "/api/chat", data=json.dumps(request).encode(),
                         headers={"Content-Type": "application/json"}), timeout=90) as response:
        for line in response:
            observation = json.loads(line)
            chunk = observation["message"]["content"]
            if chunk:
                view = session.interact(
                    "stream_delta", {"revision": view["revision"], "task_id": task["id"],
                                     "sequence": len(content), "text": chunk},
                )
                content.append(chunk)
            if observation.get("done"):
                break
    assert observation["done_reason"] == "stop"
    outputs = json.loads("".join(content))
    assert outputs == EXPECTED
    record_observation(
        tmp_path, task["idempotency_key"], task["scope_hash"],
        digest([identity, task["scope_hash"], outputs]), "succeeded", outputs,
        usage={"model_calls": 1, "cost_micro_usd": 0},
        stream={"count": len(content), "completed": True, "hash": view["pending"]["stream_hash"]},
    )
    completed = session.interact("reconcile", {"revision": view["revision"], "task_id": task["id"]})
    assert completed["outputs"] == EXPECTED
