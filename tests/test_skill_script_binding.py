import json
import sys
import zipfile

import pytest
from flow_base.graph.preparation import load_recipe, read_preparation
from flow_base.runtime import FlowError, sha256
from test_adapter_construction import _install, _node, _record
from test_capability_resolver import observed_inventory  # noqa: F401
from test_toolchain_completion import report_graph


def test_approved_complete_skill_zip_executes_the_pinned_member(observed_inventory):
    workspace = observed_inventory()
    bundle = workspace.parent / "host" / "text-skill.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("SKILL.md", "Run [the counter](scripts/count.py) on the supplied text.\n")
        archive.writestr("scripts/count.py", "import json, sys\nprint(json.dumps({'count': len(sys.argv[1])}))\n")
    recipe = {
        "recipe_schema_version": 1, "id": "fixture.skill_count", "version": "1.0.0",
        "tool": "fixture.skill_count", "platforms": [sys.platform], "action": "construct_skill_script",
        "bundle": bundle.name, "bundle_sha256": sha256(bundle), "member": "scripts/count.py",
        "arguments": [{"input": "text"}],
        "outputs": {"count": {"source": "stdout", "pointer": "/count"}},
    }
    text, count = {"type": "string", "required": True}, {"type": "integer", "required": True}
    capability = "fixture.skill.count"
    observed_inventory(_record(recipe, capability, {"text": text}, {"count": count}))
    cases = [{"inputs": {"text": value}, "expected": {"count": len(value)}} for value in ("文字", "text & 123")]
    graph = report_graph()
    graph.update(
        graph_contract_version="2.3",
        inputs={"text": text}, outputs={"count": "artifact://transform/count"},
        nodes=[_node(capability, {"text": {"source": "input://text", "port": text}}, {"count": count})],
        quality_contract={"golden_cases": cases, "adapter_cases": {
            "transform": [{"id": f"skill_{i}", **case} for i, case in enumerate(cases)],
        }},
    )
    service, _draft, installed = _install(workspace, graph)
    assert installed["qualification"]["transform"]["report"]["verdict"] == "PASS"
    inbox = workspace / "input.json"
    inbox.write_text(json.dumps({"inputs": {"text": "again"}}), encoding="utf-8")
    result = service.run_workflow_sync({"task": graph["workflow"]["id"], "source": inbox, "offline": True})
    assert result["blueprint"]["outputs"] == {"count": 5}
    runtime = service.workspace.runtime
    preparation = installed["preparations"]["transform"]
    saved = runtime.home / "toolchain_preparations" / preparation["operation_id"] / "skill.zip"
    saved.write_bytes(b"changed")
    with pytest.raises(FlowError, match="校驗值"):
        read_preparation(runtime, load_recipe(recipe))
