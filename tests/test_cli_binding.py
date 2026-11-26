import json
import shutil
import sys
import wave
from pathlib import Path

import pytest
from flow_base.blueprints.sessions import Assets
from flow_base.graph.preparation import execute_prepared, load_recipe, prepare_recipe
from flow_base.runtime import FlowError, Runtime, digest, sha256
from test_adapter_construction import _install, _node, _record, _require_program
from test_capability_resolver import observed_inventory  # noqa: F401
from test_toolchain_completion import report_graph


def _wave(path, rate):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(b"\0\0" * rate)


def _ffprobe_recipe():
    _require_program("ffprobe")
    return {
        "recipe_schema_version": 1, "id": "fixture.run_ffprobe", "version": "1.0.0",
        "tool": "fixture.ffprobe", "platforms": [sys.platform], "action": "construct_cli",
        "executable": "ffprobe", "sha256": sha256(Path(shutil.which("ffprobe"))),
        "version_args": ["-version"], "version_prefix": "ffprobe version",
        "arguments": [
            "-v", "error", "-protocol_whitelist", "file", "-show_entries",
            "stream=sample_rate", "-of", "json", "-i", {"input": "media"},
        ],
        "outputs": {"sample_rate": {"source": "stdout", "pointer": "/streams/0/sample_rate"}},
    }


def test_ffprobe_recipe_prepares_a_pinned_binary_and_reads_named_json_output(tmp_path):
    workspace = tmp_path / "中文 media & input"
    workspace.mkdir()
    runtime = Runtime(workspace / "share_states")
    audio = workspace / "clip test.wav"
    _wave(audio, 8000)
    token = Assets(runtime).store(audio)
    recipe = _ffprobe_recipe()
    receipt = prepare_recipe(runtime, load_recipe(recipe), workspace)
    binding = {
        "preparation": recipe, "implementation_hash": digest(receipt),
        "portable_adapter": {"inputs": {"media": {"type": "file"}}},
    }
    assert execute_prepared(workspace, binding, {"media": token}, {}) == {"sample_rate": "8000"}
    with pytest.raises(FlowError, match="素材名稱"):
        execute_prepared(workspace, binding, {"media": "https://example.invalid/media.wav"}, {})

    changed = {**recipe, "sha256": "0" * 64}
    with pytest.raises(FlowError, match="校驗值"):
        prepare_recipe(runtime, load_recipe(changed), workspace)


def test_ffprobe_cli_runs_through_completion_qualification_install_and_runtime(observed_inventory):
    recipe = _ffprobe_recipe()
    media = {"type": "file", "format": "wav", "required": True}
    rate = {"type": "string", "required": True}
    capability = "fixture.media.sample_rate"
    workspace = observed_inventory(_record(recipe, capability, {"media": media}, {"sample_rate": rate}))
    fixtures = workspace / "fixtures"
    fixtures.mkdir()
    runtime = Runtime(workspace / "share_states")
    graph = report_graph()
    graph.update(
        graph_contract_version="2.3",
        inputs={"media": media}, outputs={"sample_rate": "artifact://transform/sample_rate"},
        nodes=[_node(capability, {"media": {"source": "input://media", "port": media}}, {"sample_rate": rate})],
    )
    golden, adapter = [], []
    for index, sample_rate in enumerate((8000, 16000)):
        audio = fixtures / f"音訊 & {index}.wav"
        _wave(audio, sample_rate)
        expected = {"sample_rate": str(sample_rate)}
        golden.append({"inputs": {"media": {"$file": f"fixtures/{audio.name}"}}, "expected": expected})
        adapter.append({"id": f"probe_{index}", "inputs": {"media": Assets(runtime).store(audio)}, "expected": expected})
    graph["quality_contract"] = {"golden_cases": golden, "adapter_cases": {"transform": adapter}}
    service, _draft, result = _install(workspace, graph)
    assert result["qualification"]["transform"]["report"]["verdict"] == "PASS"
    request = workspace / "input.json"
    request.write_text(json.dumps({"inputs": golden[1]["inputs"]}), encoding="utf-8")
    output = service.run_workflow_sync({"task": graph["workflow"]["id"], "source": request, "offline": True})
    assert output["blueprint"]["outputs"] == golden[1]["expected"]
