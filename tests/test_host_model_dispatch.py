import asyncio
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.request import urlopen

import pytest

from flow_base.adapters.host_dispatch import dispatch_host
from flow_base.graph.external import read_observation
from test_graph_builder import data_graph, import_graph


MODEL = "gemma4:e2b"
DIGEST = "1" * 64
RESULT = "evidence-ready"


@pytest.fixture
def model_server():
    posts = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send_body(self, body, media):
            self.send_response(200)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            row = {"name": MODEL, "digest": DIGEST, "size_vram": 0}
            self.send_body(json.dumps({"models": [row]}).encode(), "application/json")

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            posts.append(request)
            inputs = json.loads(request["messages"][1]["content"])["inputs"]
            supplied = inputs["source"]
            replies = [message for message in request["messages"] if message["role"] == "tool"]
            if request.get("tools"):
                tool_text = "disconnect" if supplied == "mcp-cutoff" else "ready"
                name = request["tools"][0]["function"]["name"]
                if name == "sample_rate":
                    arguments = {"media": inputs["media"]}
                elif name == "character_count":
                    arguments = {"text": supplied}
                else:
                    arguments = {"source": tool_text}
                ident = "call_first"
                if supplied == "tool-dependency" and replies:
                    ident = "call_second"
                    arguments = {"source": json.loads(replies[0]["content"])["result"] + "-next"}
                calls = [{"id": ident, "function": {"name": name, "arguments": arguments}}]
                if supplied in {"tool-two", "tool-limit", "tool-unsafe"}:
                    name = "unauthorized" if supplied == "tool-unsafe" else "uppercase"
                    calls.append({"id": "call_second", "function": {"name": name, "arguments": {"source": "next"}}})
                rows = [{"model": MODEL, "message": {"tool_calls": calls}, "done": False}]
            else:
                result = "/".join(str(next(iter(json.loads(message["content"]).values())))
                                  for message in replies) if replies else RESULT
                chunks = ['{"result":', json.dumps(result) + "}"]
                if supplied == "invalid":
                    chunks[-1] = '4}'
                rows = [{"model": MODEL, "message": {"content": chunk}, "done": False} for chunk in chunks]
            if supplied not in {"disconnect", "tool-cutoff"} and (
                supplied != "tool-cutoff-late" or request.get("tools")
            ):
                rows.append({"model": MODEL, "message": {"content": ""}, "done": True,
                             "done_reason": "stop", "prompt_eval_count": 24, "eval_count": 9})
            self.send_body("".join(json.dumps(row) + "\n" for row in rows).encode(), "application/x-ndjson")

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", posts
        finally:
            server.shutdown()
            thread.join()


def new_job(root, catalog_file, endpoint, source, *, model=MODEL, digest=DIGEST, device="cpu", tool_mode=None,
            tool_provider="text.upper"):
    root.mkdir()
    graph = data_graph()
    node = graph["nodes"][1]
    node["capability"] = "model-echo"
    nodes = [node]
    baseline = {}
    if tool_mode:
        node["depends_on"] = ["local_upper"]
        node["permissions"] = ["tool_use"]
        graph["inputs"]["baseline"] = {"type": "string"}
        baseline = {"baseline": "before-model"}
        nodes.append({**graph["nodes"][0], "id": "local_upper", "capability": tool_provider, "inputs": {
            "source": {"source": "input://baseline", "port": {"type": "string"}},
        }})
    graph.update(nodes=nodes, outputs={"result": "artifact://copy/result"})
    elevated = device == "gpu" or bool(tool_mode)
    replies = [{"step": "copy", "kind": "external" if elevated else "host",
                "outputs": {"result": RESULT}, "deltas": [json.dumps({"result": RESULT})],
                "stream_completed": True, "usage": {"model_calls": 1, "cost_micro_usd": 0}}]
    if elevated:
        replies.insert(0, {"step": "copy", "kind": "authorize_external"})
    if tool_provider != "text.upper":
        replies.insert(0, {"step": "local_upper", "kind": "host", "outputs": {"result": "BEFORE-MODEL"}})
    graph["quality_contract"]["golden_cases"] = [{"inputs": {"text": "fixture", **baseline},
           "expected": {"result": RESULT}, "replies": replies}]
    catalog = json.loads(catalog_file.read_bytes())
    port = {"type": "string", "required": True}
    catalog["tools"]["model-echo"] = {
        **catalog["tools"]["test-sink"], "tool": "model-echo", "permissions": node["permissions"],
        "network": False, "side_effect": "none", "destination": "local-ollama",
        "inputs": {"source": port}, "outputs": {"result": port},
        "resources": {"model_calls": tool_mode.get("max_rounds", 3) if tool_mode else 1, "cost_micro_usd": 0},
        "model_profile": {"provider": "ollama", "endpoint": endpoint, "model": model, "digest": digest,
                          "device": device, "max_output_tokens": 48,
                          "instructions": (
                              f"Call the tool uppercase once with source={source!r} and wait for its result. "
                              "Use the tool response field result unchanged as the final result."
                              if tool_mode else "Copy the exact value of inputs.source into result."
                          ), **({"tool_mode": tool_mode} if tool_mode else {})},
        "result_schema": {"type": "object", "properties": {"result": {"type": "string"}},
                          "required": ["result"], "additionalProperties": False},
    }
    catalog_file.write_text(json.dumps(catalog), encoding="utf-8")
    service, key, resolution = import_graph(root, graph)
    built = service.install_workflow(draft_id=key, resolution=resolution)
    assert built["status"] == "verified" and built["evidence"]["provider_calls"] == 0
    service.install_workflow(draft_id=key, build_id=built["build_id"], install_hash=built["install_hash"])
    request = root / "input.json"
    request.write_text(json.dumps({"inputs": {"text": source, **baseline}}), encoding="utf-8")
    return service, service.run_workflow_sync({"task": "graph_copy", "source": request})["blueprint"]


@pytest.mark.parametrize("source,status", [
    (RESULT, "complete"), ("recover", "complete"), ("disconnect", "waiting"), ("invalid", "failed"),
])
def test_host_model_streams_into_installed_graph(tmp_path, desktop_test_catalog, monkeypatch, model_server, source, status):
    monkeypatch.setenv("FLOW_BASE_CODEX_SESSION_FILE", str(tmp_path / "unused-session.jsonl"))
    endpoint, posts = model_server
    root = tmp_path / "workspace"
    service, view = new_job(root, desktop_test_catalog, endpoint, source)
    task, session_id = view["pending"], view["session_id"]
    assert task["scope"]["model_profile"]["digest"] == DIGEST and not posts
    events = []
    if source == "recover":
        def lose_completion(*_args):
            raise ConnectionError("screen closed before model completion")

        with monkeypatch.context() as interrupted:
            interrupted.setattr("flow_base.adapters.host_model._reconcile", lose_completion)
            with pytest.raises(ConnectionError, match="screen closed"):
                asyncio.run(dispatch_host(service, session_id, task["id"], on_delta=events.append))
        assert service.blueprint_task(session_id, "status")["code"] == "RECONCILE_REQUIRED"
        final = asyncio.run(dispatch_host(service, session_id, task["id"]))
    else:
        final = asyncio.run(dispatch_host(service, session_id, task["id"], on_delta=events.append))
    assert final["status"] == status
    assert [event["sequence"] for event in events] == [0, 1]
    receipt = read_observation(root, task["idempotency_key"], task["scope_hash"])
    if status == "complete":
        assert final["outputs"] == {"result": RESULT}
        assert json.loads("".join(event["text"] for event in events)) == final["outputs"]
        assert final["nodes"]["copy"]["usage"] == {"model_calls": 1, "cost_micro_usd": 0,
                                                  "prompt_tokens": 24, "completion_tokens": 9}
        assert receipt["stream"]["count"] == 2
    elif status == "failed":
        assert final["nodes"]["copy"]["error"] == "EXTERNAL_FAILED"
        assert receipt["usage"]["model_calls"] == 1 and not receipt["outputs"]
    else:
        assert final["code"] == "RECONCILE_REQUIRED" and receipt is None
    assert asyncio.run(dispatch_host(service, session_id, task["id"]))["status"] == status
    assert len(posts) == 1 and posts[0]["options"] == {"temperature": 0, "num_gpu": 0, "num_predict": 48}


def test_model_gpu_choice_is_bound_to_an_authorization_gate(tmp_path, desktop_test_catalog, monkeypatch, model_server):
    from flow_base.runtime import FlowError

    monkeypatch.setenv("FLOW_BASE_CODEX_SESSION_FILE", str(tmp_path / "unused-session.jsonl"))
    endpoint, posts = model_server
    service, view = new_job(tmp_path / "workspace", desktop_test_catalog, endpoint, RESULT, device="gpu")
    pending = view["pending"]
    assert pending["kind"] == "authorize_external" and pending["scope"]["model_profile"]["device"] == "gpu"
    with pytest.raises(FlowError, match="可領取"):
        asyncio.run(dispatch_host(service, view["session_id"], pending["id"]))
    assert posts == []


def test_model_digest_drift_blocks_before_claim(tmp_path, desktop_test_catalog, monkeypatch, model_server):
    from flow_base.runtime import FlowError

    monkeypatch.setenv("FLOW_BASE_CODEX_SESSION_FILE", str(tmp_path / "unused-session.jsonl"))
    endpoint, posts = model_server
    service, initial = new_job(tmp_path / "workspace", desktop_test_catalog, endpoint, RESULT, digest="2" * 64)
    with pytest.raises(FlowError, match="指紋"):
        asyncio.run(dispatch_host(service, initial["session_id"], initial["pending"]["id"]))
    assert posts == []
    assert service.blueprint_task(initial["session_id"], "status")["pending"]["claimed"] is False


def test_portable_model_qualification_is_bound_to_execution_profile(tmp_path):
    from flow_base.graph.portable import _catalog_adapters
    from routinekeel_contracts.graph import Task
    from test_capability_resolver import adapter, csv_node

    record = adapter("model-echo")
    record["resources"] = {"model_calls": 1, "cost_micro_usd": 0}
    record["model_profile"] = {"provider": "ollama", "model": MODEL, "digest": DIGEST, "max_output_tokens": 48}
    record["portable"]["model_version"] = DIGEST
    node = Task.model_validate(csv_node())
    first, _ = _catalog_adapters(node, tmp_path, {"model-echo": record})
    record["model_profile"]["max_output_tokens"] = 64
    changed, _ = _catalog_adapters(node, tmp_path, {"model-echo": record})
    assert first[0][2] != changed[0][2]
    record["portable"]["model_version"] = "2" * 64
    incompatible, rejected = _catalog_adapters(node, tmp_path, {"model-echo": record})
    assert not incompatible and rejected[0]["field"] == "model_version"


def approve_tool_mode(service, view, *, tool="text.upper"):
    task = view["pending"]
    assert task["kind"] == "authorize_external"
    assert task["scope"]["tool_registry"]["uppercase"]["binding"]["tool"] == tool
    return service.blueprint_task(view["session_id"], "authorize", {
        "task_id": task["id"], "revision": view["revision"], "scope_hash": task["scope_hash"],
        "payload_hash": task["payload_hash"], "user_text": "同意此隔離測試執行具名本機工具",
    })


def configure_model_mcp(tmp_path, catalog_file, monkeypatch):
    from flow_base.adapters.host_inventory import refresh_configured_inventory
    from test_host_mcp_dispatch import echo_configuration

    config, calls = echo_configuration(tmp_path)
    observed = refresh_configured_inventory(config, tmp_path / "mcp-observed.json")
    catalog = json.loads(catalog_file.read_bytes())
    catalog["tools"].update(observed["tools"])
    catalog["tools"]["peer.echo"]["portable_qualification"] = {"observation": "fixture"}
    catalog_file.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setenv("ROUTINEKEEL_HOST_SOURCES", str(config))
    return calls


def enter_model_after_mcp(service, view):
    task = view["pending"]
    assert task["scope"]["provider_tool"] == "echo" and task["inputs"] == {"source": "before-model"}
    view = asyncio.run(dispatch_host(service, view["session_id"], task["id"]))
    return approve_tool_mode(service, view, tool="peer.echo")


@pytest.mark.parametrize("source,allowance,status", [
    ("tool-two", 2, "complete"), ("tool-limit", 1, "failed"),
    ("tool-unsafe", 2, "failed"), ("tool-cutoff", 2, "waiting"),
    ("tool-cutoff-late", 2, "waiting"),
])
def test_host_model_tool_round_budget_and_approval(tmp_path, desktop_test_catalog, monkeypatch,
                                                   model_server, source, allowance, status):
    from flow_base.graph.external import receipt_path
    from flow_base.runtime import FlowError, read_json

    monkeypatch.setenv("FLOW_BASE_CODEX_SESSION_FILE", str(tmp_path / "unused-session.jsonl"))
    endpoint, posts = model_server
    root = tmp_path / "workspace"
    service, view = new_job(root, desktop_test_catalog, endpoint, source, tool_mode={
        "tools": {"uppercase": "local_upper"}, "max_rounds": 2, "max_tool_calls": allowance,
    })
    with pytest.raises(FlowError, match="可領取"):
        asyncio.run(dispatch_host(service, view["session_id"], view["pending"]["id"]))
    assert not posts
    view = approve_tool_mode(service, view)
    task, session_id = view["pending"], view["session_id"]
    result = asyncio.run(dispatch_host(service, session_id, task["id"]))
    assert result["status"] == status
    operation = receipt_path(root, task["idempotency_key"])
    journal = read_json(operation.with_suffix(".tool-rounds.json"))
    assert journal["scope_hash"] == task["scope_hash"]
    if status == "complete":
        assert len(posts) == 2 and result["outputs"] == {"result": "READY/NEXT"}
        assert result["nodes"]["copy"]["usage"]["tool_calls"] == 2
        assert result["nodes"]["copy"]["usage"]["model_calls"] == 2
        calls = [message for message in posts[1]["messages"] if message["role"] == "tool"]
        assert [call["tool_call_id"] for call in calls] == ["call_first", "call_second"]
        assert [entry["outcome"] for entry in journal["tool_requests"].values()] == ["succeeded"] * 2
    elif source == "tool-cutoff-late":
        assert len(posts) == 2 and result["code"] == "RECONCILE_REQUIRED" and not operation.exists()
        assert len(journal["tool_requests"]) == 1
        assert journal["tool_requests"]["call_first"]["outputs"] == {"result": "READY"}
        assert [entry["outcome"] for entry in journal["model_requests"]] == ["observed", "unknown"]
    else:
        assert len(posts) == 1 and not journal["tool_requests"]
        if status == "waiting":
            assert result["code"] == "RECONCILE_REQUIRED" and not operation.exists()
            assert journal["model_requests"] == [{"outcome": "unknown"}]
        else:
            assert result["nodes"]["copy"]["error"] == "EXTERNAL_FAILED" and journal["rejection"]
    assert asyncio.run(dispatch_host(service, session_id, task["id"]))["status"] == status
    assert len(posts) == (2 if status == "complete" or source == "tool-cutoff-late" else 1)


@pytest.mark.parametrize("source,status", [("mcp-ready", "complete"), ("mcp-cutoff", "waiting")])
def test_model_tool_round_uses_locked_mcp_and_preserves_unknown_receipt(
    tmp_path, desktop_test_catalog, monkeypatch, model_server, source, status,
):
    from flow_base.graph.external import receipt_path
    from flow_base.runtime import read_json

    monkeypatch.setenv("FLOW_BASE_CODEX_SESSION_FILE", str(tmp_path / "unused-session.jsonl"))
    calls = configure_model_mcp(tmp_path, desktop_test_catalog, monkeypatch)
    endpoint, posts = model_server
    root = tmp_path / "workspace"
    service, view = new_job(root, desktop_test_catalog, endpoint, source, tool_provider="peer.echo",
                            tool_mode={"tools": {"uppercase": "local_upper"}, "max_rounds": 2, "max_tool_calls": 1})
    assert not calls.exists() and not posts
    view = enter_model_after_mcp(service, view)
    task, session_id = view["pending"], view["session_id"]
    final = asyncio.run(dispatch_host(service, session_id, task["id"]))
    assert final["status"] == status
    operation = receipt_path(root, task["idempotency_key"])
    journal = read_json(operation.with_suffix(".tool-rounds.json"))
    entry = journal["tool_requests"]["call_first"]
    assert entry["operation_key"] != task["idempotency_key"]
    assert asyncio.run(dispatch_host(service, session_id, task["id"]))["status"] == status
    if status == "complete":
        assert final["outputs"] == {"result": "READY"}
        assert len(posts) == 2 and entry["provider_receipt"] and entry["outcome"] == "succeeded"
        assert calls.read_text(encoding="utf-8").splitlines() == ["before-model", "ready"]
    else:
        assert final["code"] == "RECONCILE_REQUIRED" and not operation.exists()
        assert len(posts) == 1 and entry["outcome"] == "unknown" and entry["error"] == "TOOL_RESULT_UNKNOWN"
        assert calls.read_text(encoding="utf-8").splitlines() == ["before-model", "disconnect"]


def test_dependent_tool_round_uses_the_previous_result(tmp_path, desktop_test_catalog, monkeypatch, model_server):
    from flow_base.graph.external import receipt_path
    from flow_base.runtime import read_json

    monkeypatch.setenv("FLOW_BASE_CODEX_SESSION_FILE", str(tmp_path / "unused-session.jsonl"))
    endpoint, posts = model_server
    root = tmp_path / "workspace"
    service, view = new_job(root, desktop_test_catalog, endpoint, "tool-dependency", tool_mode={
        "tools": {"uppercase": "local_upper"}, "max_rounds": 3, "max_tool_calls": 2,
    })
    view = approve_tool_mode(service, view)
    task = view["pending"]
    final = asyncio.run(dispatch_host(service, view["session_id"], task["id"]))
    assert final["outputs"] == {"result": "READY/READY-NEXT"}
    operation = receipt_path(root, task["idempotency_key"])
    journal = read_json(operation.with_suffix(".tool-rounds.json"))
    assert journal["tool_requests"]["call_second"]["arguments"] == {"source": "READY-next"}
    assert final["nodes"]["copy"]["usage"]["model_calls"] == len(posts) == 3


@pytest.mark.skipif(not os.environ.get("ROUTINEKEEL_TEST_OLLAMA_MODEL"), reason="local model integration opt-in")
def test_cli_streams_from_real_local_ollama(tmp_path, desktop_test_catalog, monkeypatch):
    monkeypatch.setenv("FLOW_BASE_CODEX_SESSION_FILE", str(tmp_path / "unused-session.jsonl"))
    model = os.environ["ROUTINEKEEL_TEST_OLLAMA_MODEL"]
    endpoint = "http://127.0.0.1:11434"
    with urlopen(endpoint + "/api/tags", timeout=5) as response:
        digest = next(row["digest"] for row in json.load(response)["models"] if row["name"] == model)
    root = tmp_path / "workspace"
    _, view = new_job(root, desktop_test_catalog, endpoint, RESULT, model=model, digest=digest)
    task = view["pending"]
    process = subprocess.run([sys.executable, "-m", "flow_base", "--workspace", str(root),
                              "host-dispatch", view["session_id"], task["id"], "--stream"],
                             encoding="utf-8", capture_output=True, timeout=145)
    assert process.returncode == 0, process.stdout + process.stderr
    final = json.loads(process.stdout)["result"]
    events = [json.loads(line) for line in process.stderr.splitlines() if line.startswith('{"type": "delta"')]
    assert len(events) > 1 and json.loads("".join(event["text"] for event in events)) == {"result": RESULT}
    assert final["outputs"] == {"result": RESULT} and final["run_id"]
    receipt = read_observation(root, task["idempotency_key"], task["scope_hash"])
    assert receipt["usage"]["model_calls"] == 1 and receipt["usage"]["completion_tokens"] > 0
    assert receipt["usage"]["prompt_tokens"] > 0 and receipt["stream"]["count"] == len(events)


@pytest.mark.parametrize("tool_provider", ["text.upper", "peer.echo"])
@pytest.mark.skipif(not os.environ.get("ROUTINEKEEL_TEST_OLLAMA_MODEL"), reason="local model integration opt-in")
def test_cli_roundtrip_from_real_local_ollama(tmp_path, desktop_test_catalog, monkeypatch, tool_provider):
    from flow_base.graph.external import receipt_path
    from flow_base.runtime import read_json

    monkeypatch.setenv("FLOW_BASE_CODEX_SESSION_FILE", str(tmp_path / "unused-session.jsonl"))
    model = os.environ["ROUTINEKEEL_TEST_OLLAMA_MODEL"]
    endpoint = "http://127.0.0.1:11434"
    with urlopen(endpoint + "/api/tags", timeout=5) as response:
        digest = next(row["digest"] for row in json.load(response)["models"] if row["name"] == model)
    calls = configure_model_mcp(tmp_path, desktop_test_catalog, monkeypatch) if tool_provider == "peer.echo" else None
    root = tmp_path / "workspace"
    service, view = new_job(root, desktop_test_catalog, endpoint, RESULT, model=model, digest=digest,
                            tool_provider=tool_provider, tool_mode={
        "tools": {"uppercase": "local_upper"}, "max_rounds": 2, "max_tool_calls": 1,
    })
    view = enter_model_after_mcp(service, view) if calls else approve_tool_mode(service, view)
    task = view["pending"]
    process = subprocess.run([sys.executable, "-m", "flow_base", "--workspace", str(root),
                              "host-dispatch", view["session_id"], task["id"], "--stream"],
                             encoding="utf-8", capture_output=True, timeout=200)
    assert process.returncode == 0, process.stdout + process.stderr
    final = json.loads(process.stdout)["result"]
    assert final.get("outputs") == {"result": RESULT.upper()}, (final.get("status"), process.stderr)
    operation = receipt_path(root, task["idempotency_key"])
    journal = read_json(operation.with_suffix(".tool-rounds.json"))
    receipt = read_observation(root, task["idempotency_key"], task["scope_hash"])
    assert receipt["usage"]["model_calls"] == 2 and receipt["usage"]["tool_calls"] == 1
    tool = next(iter(journal["tool_requests"].values()))
    assert tool["name"] == "uppercase" and tool["outputs"] == final["outputs"]
    if calls:
        assert tool["provider_receipt"] and calls.read_text(encoding="utf-8").splitlines() == ["before-model", RESULT]
