import asyncio
import json
import sys
import zipfile

import pytest

from flow_base.adapters.host_dispatch import dispatch_host
from flow_base.blueprints.sessions import Assets
from flow_base.graph.external import receipt_path
from flow_base.runtime import Runtime, read_json, sha256
from test_adapter_construction import _install, _node, _record
from test_capability_resolver import observed_inventory  # noqa: F401
from test_cli_binding import _ffprobe_recipe, _wave
from test_host_model_dispatch import DIGEST, MODEL, model_server  # noqa: F401
from test_toolchain_completion import report_graph


TEXT = {"type": "string", "required": True}


def _python_script(workspace):
    bundle = workspace.parent / "host" / "counting-skill.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("SKILL.md", "Use the supplied count script to count the input characters.\n")
        archive.writestr("scripts/count.py", "import json, sys\nprint(json.dumps({'count': len(sys.argv[1])}))\n")
    return {"recipe_schema_version": 1, "id": "fixture.counting_script", "version": "1.0.0",
            "tool": "fixture.counting_script", "action": "construct_skill_script", "platforms": [sys.platform],
            "bundle": bundle.name, "bundle_sha256": sha256(bundle), "member": "scripts/count.py",
            "arguments": [{"input": "text"}], "outputs": {"count": {"source": "stdout", "pointer": "/count"}}}


def _media_cases(workspace):
    assets = Assets(Runtime(workspace / "share_states"))
    fixtures = workspace / "fixtures"
    fixtures.mkdir()
    cases = []
    for rate in (8000, 16000):
        audio = fixtures / f"case-{rate}.wav"
        _wave(audio, rate)
        cases.append(({"$file": f"fixtures/{audio.name}"}, assets.store(audio), {"sample_rate": str(rate)}))
    return cases


@pytest.mark.parametrize("backend", ["python", "ffprobe"])
def test_bound_adapter_runs_in_host_model_tool_round(observed_inventory, monkeypatch, model_server, backend):
    workspace = observed_inventory()
    monkeypatch.setenv("FLOW_BASE_CODEX_SESSION_FILE", str(workspace / "unused-session.jsonl"))
    endpoint, posts = model_server
    if backend == "python":
        alias, inlet, input_port, capability = "character_count", "text", TEXT, "fixture.python_count"
        recipe = _python_script(workspace)
        output_ports = {"count": {"type": "integer", "required": True}}
        examples = [(value, value, {"count": len(value)}) for value in ("seed", "verified")]
    else:
        alias, inlet, capability = "sample_rate", "media", "fixture.media_rate"
        input_port = {"type": "file", "format": "wav", "required": True}
        recipe, output_ports, examples = _ffprobe_recipe(), {"sample_rate": TEXT}, _media_cases(workspace)

    (baseline, _, baseline_output), (tool_input, tool_value, tool_output) = examples
    expected = {"result": str(next(iter(tool_output.values())))}
    model_inputs = {"source": TEXT, **({"media": input_port} if backend == "ffprobe" else {})}
    model = {
        "tool": "fixture.tool_caller", "version": "1", "receipt_id": "local-model-observation",
        "available": True, "allowed": True, "platforms": [sys.platform], "permissions": [],
        "side_effect": "none", "network": False, "inputs": model_inputs, "outputs": {"result": TEXT},
        "resources": {"model_calls": 2, "cost_micro_usd": 0},
        "model_profile": {"provider": "ollama", "endpoint": endpoint, "model": MODEL, "digest": DIGEST,
                          "max_output_tokens": 48,
                          "tool_mode": {"tools": {alias: "transform"}, "max_rounds": 2, "max_tool_calls": 1}},
        "result_schema": {"type": "object", "properties": {"result": {"type": "string"}},
                          "required": ["result"], "additionalProperties": False},
    }
    observed_inventory(_record(recipe, capability, {inlet: input_port}, output_ports), model)
    graph_inputs = {"baseline": input_port, **model_inputs}
    case = {"baseline": baseline, "source": "verified", **({"media": tool_input} if backend == "ffprobe" else {})}
    graph = report_graph()
    graph.update(graph_contract_version="2.3", inputs=graph_inputs, outputs={"result": "artifact://model/result"}, nodes=[
        _node(capability, {inlet: {"source": "input://baseline", "port": input_port}}, output_ports),
        {"id": "model", "kind": "task", "capability": model["tool"], "capability_version": "1",
         "depends_on": ["transform"], "permissions": [], "side_effect": "none", "outputs": model["outputs"],
         "inputs": {name: {"source": f"input://{name}", "port": port} for name, port in model_inputs.items()}},
    ])
    graph["quality_contract"] = {
        "golden_cases": [{"inputs": case, "expected": expected, "replies": [{
            "step": "model", "kind": "host", "outputs": expected, "deltas": [json.dumps(expected)],
            "stream_completed": True, "usage": {"model_calls": 1, "cost_micro_usd": 0},
        }]}],
        "adapter_cases": {"transform": [
            {"id": f"fixture_{index}", "inputs": {inlet: value}, "expected": outputs}
            for index, (_, value, outputs) in enumerate(examples)
        ]},
    }
    service, _, installed = _install(workspace, graph)
    assert installed["qualification"]["transform"]["report"]["verdict"] == "PASS"
    request = workspace / "input.json"
    request.write_text(json.dumps({"inputs": case}), encoding="utf-8")
    view = service.run_workflow_sync({"task": graph["workflow"]["id"], "source": request, "offline": True})["blueprint"]
    assert view["nodes"]["transform"]["outputs"] == baseline_output
    pending = view["pending"]
    target = pending["scope"]["tool_registry"][alias]["binding"]
    assert target["provider"] == "portable_local" and target["qualification_receipt"]
    result = asyncio.run(dispatch_host(service, view["session_id"], pending["id"]))
    assert result["outputs"] == expected and len(posts) == 2
    operation = receipt_path(workspace, pending["idempotency_key"])
    journal = read_json(operation.with_suffix(".tool-rounds.json"))
    call = journal["tool_requests"]["call_first"]
    assert call["outputs"] == tool_output and call["arguments"] == {inlet: tool_value}
    assert call["outcome"] == "succeeded"
