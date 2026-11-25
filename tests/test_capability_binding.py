import json
import sys
import zipfile

import pytest
from flow_base.core.service import WorkflowService
from flow_base.graph.capabilities import validate_bindings
from flow_base.runtime import FlowError
from test_blueprint_install import apply, candidate, resolution
from test_graph_contracts import example


def test_observation_uses_an_actual_mcp_tool_listing_without_running_workflows(tmp_path):
    import asyncio

    from flow_base.adapters.host_inventory import merge_tool_observations, observe_tools
    from test_mcp_stdio import client_for

    async def capture():
        grants = {
            "list_workflows": {
                "allowed": True,
                "network": False,
                "platforms": [sys.platform],
                "permissions": [],
                "side_effect": "none",
            }
        }
        async with client_for(tmp_path) as client:
            legacy = await observe_tools(client, grants)
            first = await observe_tools(
                client,
                grants,
                source_id="first",
                routes={"list_workflows": "first.list_workflows"},
            )
        async with client_for(tmp_path) as client:
            second = await observe_tools(
                client,
                grants,
                source_id="second",
                routes={"list_workflows": "second.list_workflows"},
            )
        with pytest.raises(FlowError, match="不同派送名稱"):
            merge_tool_observations(first, first)
        return legacy, merge_tool_observations(first, second)

    catalog, merged = asyncio.run(capture())
    entry = catalog["tools"]["list_workflows"]
    assert entry["observed"] == "tools/list" and entry["input_schema"]["type"] == "object"
    assert entry["server"] == "RoutineKeel" and len(entry["receipt_id"]) == 64
    sources = merged["tools"]
    assert {key: item["source_id"] for key, item in sources.items()} == {
        "first.list_workflows": "first", "second.list_workflows": "second",
    }
    assert {item["provider_tool"] for item in sources.values()} == {"list_workflows"}
    assert len({item["receipt_id"] for item in sources.values()}) == 2
    assert not list(tmp_path.iterdir())


def test_actual_imported_graph_resolves_offline_builtin_without_human_review_per_node(tmp_path):
    data = example()
    path = tmp_path / "draft.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("test/blueprint.json", json.dumps(data))
    service = WorkflowService(tmp_path)
    draft = service.install_workflow(archive=path)["draft_id"]
    report = service.resolve_blueprint_capabilities(draft, offline=True)
    assert (
        report["status"] == "ready" and set(report["bindings"]) == {"copy"} and not report["gaps"]
    )
    assert report["bindings"]["copy"]["provider"] == "builtin"


def test_configured_host_source_discovers_tools_and_rechecks_changed_grants(tmp_path, monkeypatch):
    import asyncio
    from datetime import UTC, datetime, timedelta

    pytest.importorskip("mcp")
    from flow_base.graph.capabilities import capability_observation, host_inventory_path

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "mcp-sources.json"
    source = {"command": sys.executable, "args": ["-m", "flow_base", "--workspace", str(workspace), "mcp-stdio"],
              "grants": {"list_workflows": {"allowed": True}}, "routes": {"list_workflows": "peer.list"}}
    monkeypatch.delenv("ROUTINEKEEL_HOST_CATALOG", raising=False)
    monkeypatch.setenv("ROUTINEKEEL_HOST_SOURCES", str(config))
    config.write_text(json.dumps({"version": 1, "sources": {"peer": source}}), encoding="utf-8")

    async def resolve():
        return capability_observation(workspace)

    tools, first = asyncio.run(resolve())
    assert first["status"] == "current" and list(tools) == ["peer.list"]
    assert tools["peer.list"]["observed"] == "tools/list"
    assert tools["peer.list"]["allowed"] is True
    cache = host_inventory_path(workspace)
    stored = json.loads(cache.read_bytes())
    stored["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    cache.write_text(json.dumps(stored), encoding="utf-8")
    assert capability_observation(workspace)[1]["id"] == first["id"]

    source["grants"]["list_workflows"]["allowed"] = False
    config.write_text(json.dumps({"version": 1, "sources": {"peer": source}}), encoding="utf-8")
    tools, second = capability_observation(workspace)
    assert second["id"] != first["id"] and tools["peer.list"]["allowed"] is False
    assert not list(workspace.iterdir())


@pytest.mark.parametrize(
    "fault",
    ["missing", "observed", "version", "offline", "denied", "platform", "expired", "workspace"],
)
def test_host_observations_and_permissions_are_independent_of_blueprint_claims(
    tmp_path, desktop_test_catalog, monkeypatch, fault
):
    b = resolution()["bindings"]
    data = json.loads(desktop_test_catalog.read_bytes())
    record = data["tools"]["test-sink"]
    if fault == "missing":
        data["tools"] = {}
    elif fault == "observed":
        b["post"]["probe"]["observed"] = "different"
    elif fault == "version":
        record["version"] = "2"
    elif fault == "offline":
        record["network"] = True
    elif fault == "denied":
        record["allowed"] = False
    elif fault == "platform":
        record["platforms"] = ["not-" + sys.platform]
    elif fault == "expired":
        data["expires_at"] = "2000-01-01T00:00:00+00:00"
    elif fault == "workspace":
        monkeypatch.setenv("ROUTINEKEEL_HOST_CATALOG", str(tmp_path / "claimed.json"))
        (tmp_path / "claimed.json").write_text(json.dumps(data))
    desktop_test_catalog.write_text(json.dumps(data))
    with pytest.raises(FlowError):
        validate_bindings(tmp_path, b, offline=fault == "offline")


def test_lost_permission_after_trial_blocks_promotion(tmp_path, desktop_test_catalog):
    service, draft, built = candidate(tmp_path)
    assert built["capability_evidence"]["post"]["provider"] == "desktop_host"
    data = json.loads(desktop_test_catalog.read_bytes())
    data["tools"]["test-sink"]["allowed"] = False
    desktop_test_catalog.write_text(json.dumps(data))
    with pytest.raises(FlowError):
        apply(service, draft, built)
    assert not service.workspace.discover(read_only=True)
