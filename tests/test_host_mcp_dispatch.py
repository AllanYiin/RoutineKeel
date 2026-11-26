import asyncio
import json
import subprocess
import sys

import pytest

from flow_base.adapters.host_dispatch import dispatch_mcp
from flow_base.core.service import WorkflowService
from test_graph_builder import data_graph, import_graph


SERVER = '''
import json, sys
from pathlib import Path

journal = Path(sys.argv[1])
advertised = sys.argv[2] == "advertise"
schema = {"type": "object", "properties": {"echoed": {"type": "string"}}, "required": ["echoed"]}
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    params = request.get("params", {})
    match request["method"]:
        case "initialize":
            result = {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}},
                      "serverInfo": {"name": "RK echo", "version": "1"}}
        case "tools/list":
            tool = {"name": "echo", "inputSchema": {"type": "object", "required": ["source"],
                    "properties": {"source": {"type": "string"}}}}
            if advertised:
                tool["outputSchema"] = schema
            result = {"tools": [tool]}
        case "tools/call":
            text = params["arguments"]["source"]
            assert len(params["_meta"]["routinekeel/idempotencyKey"]) == 64
            with journal.open("a", encoding="utf-8") as file:
                file.write(text + "\\n")
            if text == "disconnect":
                break
            if text == "error":
                result = {"isError": True, "content": [{"type": "text", "text": "tool refused"}]}
            elif text == "input-required":
                result = {"resultType": "input_required", "requestState": "pending"}
            else:
                result = {"content": [], "structuredContent": {"echoed": text.upper()}}
        case _:
            result = {}
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
'''


def echo_configuration(tmp_path, *, advertised=True):
    config = tmp_path / "sources.json"
    server = tmp_path / "echo_server.py"
    journal = tmp_path / "calls.txt"
    server.write_text(SERVER, encoding="utf-8")
    port = {"type": "string", "required": True}
    grant = {"allowed": True, "network": False, "side_effect": "none", "platforms": [sys.platform],
             "permissions": [], "inputs": {"source": port}, "outputs": {"result": port},
             "output_mapping": {"result": "echoed"}}
    if not advertised:
        grant["result_schema"] = {"type": "object", "required": ["echoed"],
                                  "properties": {"echoed": {"type": "string"}}}
    source = {
        "command": sys.executable,
        "args": ["-I", str(server), str(journal), "advertise" if advertised else "mapped"],
        "grants": {"echo": grant}, "routes": {"echo": "peer.echo"},
    }
    config.write_text(json.dumps({"version": 1, "sources": {"peer": source}}), encoding="utf-8")
    return config, journal


@pytest.mark.parametrize(
    "advertised,text,expected,entry", [
        (True, "hello", "complete", "cli"),
        (False, "hello", "complete", "python"),
        (True, "error", "failed", "python"),
        (True, "disconnect", "waiting", "python"),
        (True, "input-required", "waiting", "python"),
        (True, "hello", "complete", "lost-completion"),
        (True, "hello", "complete", "race"),
    ],
)
def test_configured_mcp_dispatch_and_reconcile(tmp_path, monkeypatch, advertised, text, expected, entry):
    root = tmp_path / "workspace"
    root.mkdir()
    config, journal = echo_configuration(tmp_path, advertised=advertised)
    monkeypatch.delenv("ROUTINEKEEL_HOST_CATALOG", raising=False)
    monkeypatch.setenv("ROUTINEKEEL_HOST_SOURCES", str(config))
    monkeypatch.setenv("FLOW_BASE_CODEX_SESSION_FILE", str(tmp_path / "unused-session.jsonl"))

    graph = data_graph()
    node = graph["nodes"][1]
    node.update(capability="peer.echo")
    graph.update(nodes=[node], outputs={"result": "artifact://copy/result"})
    graph["quality_contract"]["golden_cases"] = [{
        "inputs": {"text": "fixture"}, "expected": {"result": "FIXTURE"},
        "replies": [{"step": "copy", "kind": "host", "outputs": {"result": "FIXTURE"}}],
    }]
    service, draft, resolution = import_graph(root, graph)
    candidate = service.install_workflow(draft_id=draft, resolution=resolution)
    assert candidate["status"] == "verified" and candidate["evidence"]["provider_calls"] == 0
    service.install_workflow(draft_id=draft, build_id=candidate["build_id"], install_hash=candidate["install_hash"])
    request = root / "input.json"
    request.write_text(json.dumps({"inputs": {"text": text}}), encoding="utf-8")
    view = service.run_workflow_sync({"task": "graph_copy", "source": request})["blueprint"]
    task_id, session_id = view["pending"]["id"], view["session_id"]
    assert not journal.exists()
    if entry == "cli":
        process = subprocess.run(
            [sys.executable, "-m", "flow_base", "--workspace", str(root), "host-dispatch", session_id, task_id],
            text=True, encoding="utf-8", capture_output=True, timeout=45,
        )
        assert process.returncode == 0, process.stdout + process.stderr
        result = json.loads(process.stdout)["result"]
    elif entry == "lost-completion":
        def disconnect(*_args):
            raise ConnectionError("host exited after producing its receipt")

        with monkeypatch.context() as interrupted:
            interrupted.setattr("flow_base.adapters.host_dispatch._reconcile", disconnect)
            with pytest.raises(ConnectionError, match="host exited"):
                asyncio.run(dispatch_mcp(WorkflowService(root), session_id, task_id))
        assert WorkflowService(root).blueprint_task(session_id, "status")["code"] == "RECONCILE_REQUIRED"
        result = asyncio.run(dispatch_mcp(WorkflowService(root), session_id, task_id))
    elif entry == "race":
        async def race():
            return await asyncio.gather(*(dispatch_mcp(WorkflowService(root), session_id, task_id) for _ in range(2)))

        result = next(view for view in asyncio.run(race()) if view.get("run_id"))
    else:
        result = asyncio.run(dispatch_mcp(WorkflowService(root), session_id, task_id))
    assert result["status"] == expected
    if expected == "complete":
        assert result["outputs"] == {"result": "HELLO"}
        assert result["run_id"]
    elif expected == "failed":
        assert result["nodes"]["copy"]["error"] == "EXTERNAL_FAILED"
    else:
        assert result["code"] == "RECONCILE_REQUIRED"
    retry = asyncio.run(dispatch_mcp(WorkflowService(root), session_id, task_id))
    assert retry["status"] == expected
    assert journal.read_text(encoding="utf-8").splitlines() == [text]
