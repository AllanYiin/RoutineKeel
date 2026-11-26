"""Observe the dedicated copyediting tool over a real local MCP connection."""

import asyncio
import argparse
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import sys
from time import monotonic
from tempfile import TemporaryDirectory
from urllib.request import urlopen

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from flow_base.graph.external import record_observation
from flow_base.graph.session import GraphSession
from flow_base.runtime import digest, read_json
from flow_base.workspace import Workspace
from routinekeel_contracts.domain_assets import content_hash


@asynccontextmanager
async def connect(journal):
    provider = Path(__file__).with_name("course_subtitles_host.py")
    command = StdioServerParameters(
        command=sys.executable, args=["-I", "-B", "-X", "utf8", str(provider), "--journal-dir", str(journal)]
    )
    async with stdio_client(command) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            connection = await session.initialize()
            yield session, connection, provider


async def dispatch(workspace, claimed, journal, *, reconcile=False):
    """Record the provider result for an already claimed RoutineKeel operation."""
    request = claimed["dispatch"]
    budget = request["arguments"]["model_budget"]
    if (request["tool"] != "course_ollama_copyedit"
            or any(request[key] != budget[key] for key in ("scope_hash", "idempotency_key"))):
        raise ValueError("Dispatch budget and claimed operation do not match")
    state = read_json(GraphSession(Workspace(workspace).runtime, claimed["session_id"]).path)
    task = next((task for task in state["pending_tasks"].values()
                 if task.get("idempotency_key") == budget["idempotency_key"]), None)
    if (
        not task or not task.get("claimed") or task["scope_hash"] != budget["scope_hash"]
        or digest(task["scope"]) != budget["scope_hash"]
        or task["scope"]["tool"] != request["tool"]
        or task["scope"]["resources"]["model_calls"] != budget["max_calls"]
        or request["arguments"] != {**task["inputs"], "model_budget": budget}
    ):
        raise ValueError("Run checkpoint does not authorize this model dispatch")
    async with connect(journal) as (session, _, _):
        reply = await session.call_tool(
            "course_copyedit_receipt" if reconcile else "course_ollama_copyedit",
            {"model_budget": budget} if reconcile else request["arguments"],
            read_timeout_seconds=max(30, budget["max_calls"] * 185),
        )
    result = reply.structured_content
    if reply.is_error or not result:
        raise ValueError("Course host has no verifiable receipt")
    if result["outcome"] == "unknown":
        return {"outcome": "unknown", "completed_segments": result.get("completed_segments"),
                "measurement": result.get("measurement")}
    if any(result[key] != budget[key] for key in ("scope_hash", "idempotency_key")):
        raise ValueError("Receipt belongs to another operation")
    measurement = result["measurement"]
    record_observation(
        workspace, budget["idempotency_key"], budget["scope_hash"], content_hash(result),
        result["outcome"], result["outputs"], measurement=measurement,
        usage={"model_calls": measurement["model_calls"], "cost_micro_usd": 0},
    )
    return {"outcome": result["outcome"], "measurement": measurement}


async def observe(cases, journal):
    tool_name = "course_ollama_copyedit"
    async with connect(journal) as (session, connection, provider):
        tools = await session.list_tools()
        tool = next(tool for tool in tools.tools if tool.name == tool_name)
        facts = {
            "server": connection.server_info.name,
            "version": connection.server_info.version,
            "tool": tool.name,
            "input_schema": tool.input_schema,
            "output_schema": tool.output_schema,
            "implementation_sha256": hashlib.sha256(provider.read_bytes()).hexdigest(),
            "model_call_batch": {"input": "segments", "size": 4},
        }
        with urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as response:
            models = json.load(response)["models"]
        model = next(item for item in models if item["name"] == "gemma4:e2b")
        facts["model_version"] = model["digest"]
        observations = []
        for case in cases:
            started = monotonic()
            calls = (len(case["inputs"]["segments"]) + 3) // 4
            identity = content_hash(["qualification", facts, case])
            arguments = {**case["inputs"], "model_budget": {
                "idempotency_key": identity, "scope_hash": identity, "max_calls": calls,
            }}
            result = await session.call_tool(tool_name, arguments, read_timeout_seconds=max(30, calls * 185))
            if result.is_error or not result.structured_content:
                raise ValueError(result.content)
            receipt = result.structured_content
            if (receipt.get("idempotency_key"), receipt.get("scope_hash")) != (identity, identity):
                raise ValueError("Observation belongs to another qualification attempt")
            observations.append({
                "id": case["id"], "output": receipt["outputs"],
                "measurement": receipt["measurement"], "outcome": receipt["outcome"],
                "error": receipt.get("error"),
                "elapsed_ms": round((monotonic() - started) * 1000),
            })
    return {"facts": facts, "observations": observations}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", nargs="?", type=Path)
    parser.add_argument("--journal-parent", type=Path)
    parser.add_argument("--claimed-dispatch", type=Path)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--reconcile", action="store_true")
    args = parser.parse_args()
    if args.claimed_dispatch:
        if not args.workspace or not args.journal:
            parser.error("--claimed-dispatch requires --workspace and a persistent --journal")
        request = json.loads(args.claimed_dispatch.read_text("utf-8-sig"))
        result = asyncio.run(dispatch(args.workspace, request, args.journal, reconcile=args.reconcile))
    else:
        cases = json.loads(args.cases.read_text("utf-8-sig")) if args.cases else []
        with TemporaryDirectory(prefix="rk-q-", dir=args.journal_parent) as folder:
            result = asyncio.run(observe(cases, Path(folder)))
    print(json.dumps(result, ensure_ascii=True))
