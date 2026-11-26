"""Build a time-limited host inventory from the installed entries and MCP observation."""

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import sys

from flow_base.graph.portable import local_entry
from flow_base.runtime import atomic_json
from routinekeel_contracts.bindings import ImplementationAdapter
from routinekeel_contracts.domain_assets import content_hash
from routinekeel_contracts.graph_validator import validate_blueprint


def inventory(graph_path, facts, previous):
    source = json.loads(graph_path.read_text("utf-8"))
    graph, _ = validate_blueprint(source["graph"])
    tools = {}
    for node in graph.nodes:
        requirement = getattr(node, "capability_requirement", None)
        if requirement is None:
            continue
        host = node.id == "edit_transcript"
        if host and not facts:
            continue
        name = facts["tool"] if host else f"course.local.{node.id}"
        version = facts["version"] if host else "0.1.0"
        permissions = []
        inputs = {key: value.port.model_dump(exclude_none=True) for key, value in node.inputs.items()}
        outputs = {key: value.model_dump(exclude_none=True) for key, value in node.outputs.items()}
        adapter = ImplementationAdapter(
            id=f"course.adapter.{node.id}",
            version=version,
            capability=requirement.capability,
            capability_version="1.0.0",
            provider_version=version,
            model_version=facts["model_version"] if host else None,
            locus="host_mediated" if host else "local_runtime",
            tool=name,
            distribution=None if host else "routinekeel-course-subtitles-adapters",
            inputs={key: dict(port, schema_id=requirement.input_semantics.get(key))
                    for key, port in inputs.items()},
            outputs={key: dict(port, schema_id=requirement.output_semantics.get(key))
                     for key, port in outputs.items()},
            permissions=permissions,
            side_effect=node.side_effect,
            network=False,
            platforms=[sys.platform],
            max_cost_micro_usd=0,
        )
        observation = dict(facts) if host else {"installed_entry": (local_entry(adapter) or (None, None))[1]}
        if not host and observation["installed_entry"] is None:
            continue
        tool = {
            "version": version,
            "receipt_id": content_hash(observation),
            "observed": "tools/list" if host else "entry_points",
            "available": True,
            "allowed": True,
            "platforms": [sys.platform],
            "permissions": permissions,
            "side_effect": node.side_effect,
            "network": False,
            "resources": {"network": False, "cost_micro_usd": 0, "model_calls": int(host)},
            "inputs": inputs,
            "outputs": outputs,
            "portable": adapter.model_dump(mode="json"),
        }
        if host:
            rule = facts.get("model_call_batch")
            if rule != {"input": "segments", "size": 4} or version != "0.2.0":
                raise ValueError("Update the copyediting tool and observe its current batch contract first")
            tool["resources"] = {"network": False, "cost_micro_usd": 0, "model_call_batch": rule}
            tool.update(
                server=facts["server"],
                input_schema=facts["input_schema"],
                output_schema=facts["output_schema"],
                destination="local Ollama",
                scope_hash=content_hash(["course-video-subtitles", "minimal transcript text", "local Ollama"]),
                parameters_schema={
                    "type": "object",
                    "properties": {key: {"const": value} for key, value in node.parameters.items()},
                    "required": list(node.parameters),
                    "additionalProperties": False,
                },
            )
            older = previous.get(name, {})
            if all(older.get(key) == value for key, value in tool.items()):
                if qualified := older.get("portable_qualification"):
                    tool["portable_qualification"] = qualified
        tools[name] = tool
    now = datetime.now(UTC)
    return {
        "version": 1, "observed_at": now.isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(), "tools": tools,
    }


if __name__ == "__main__":
    graph_path, facts_path, target, *prior = map(Path, sys.argv[1:])
    facts = json.loads(facts_path.read_text("utf-8"))["facts"] if facts_path.is_file() else {}
    previous = json.loads(prior[0].read_bytes())["tools"] if prior and prior[0].is_file() else {}
    atomic_json(target, inventory(graph_path, facts, previous))
    print(os.fspath(target))
