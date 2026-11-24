import hashlib
import json
import shutil
import sys
import zipfile
from datetime import UTC, datetime, timedelta
from importlib.metadata import entry_points
from pathlib import Path

import pytest
from flow_base.core.service import WorkflowService
from flow_base.graph.portable import ADAPTER_GROUP, resolve_requirement
from flow_base.runtime import FlowError
from routinekeel_contracts.domain_assets import CapabilityRequirement, content_hash
from routinekeel_contracts.graph import Task

PORT = {"type": "string", "required": True}
ROWS = {"type": "array", "required": True}


def csv_node():
    return {
        "id": "rwp_reader",
        "kind": "task",
        "capability": "tabular.read",
        "capability_version": "1.0.0",
        "permissions": [],
        "side_effect": "none",
        "inputs": {"source": {"source": "input://csv", "port": PORT}},
        "outputs": {"rows": ROWS},
    }


def requirement(**options):
    return CapabilityRequirement.model_validate(
        {
            "id": "read",
            "capability": "tabular.read",
            "version": "1.0.0",
            "required": True,
            "portability": "flexible",
            **options,
        }
    )


def adapter(
    tool,
    *,
    locus="host_mediated",
    allowed=True,
    numeric="decimal",
    age=10,
    cap="tabular.read",
    permissions=None,
    network=False,
):
    scope = [] if permissions is None else permissions
    name = tool if "." in tool else f"observed.{tool}"
    descriptor = {
        "id": name,
        "version": "1.0.0",
        "capability": cap,
        "capability_version": "1.0.0",
        "provider_version": "1.0.0",
        "locus": locus,
        "tool": tool,
        "distribution": "routinekeel-data-adapter" if locus == "local_runtime" else None,
        "inputs": {"source": PORT},
        "outputs": {"rows": ROWS},
        "permissions": scope,
        "side_effect": "none",
        "network": network,
        "platforms": [sys.platform],
        "max_cost_micro_usd": 0,
        "max_latency_ms": 100,
        "max_data_age_seconds": age,
        "numeric_representation": numeric,
    }
    return {
        "tool": tool,
        "version": "1.0.0",
        "receipt_id": "fixture-observed-schema",
        "available": True,
        "allowed": allowed,
        "platforms": [sys.platform],
        "permissions": scope,
        "side_effect": "none",
        "network": network,
        "inputs": descriptor["inputs"],
        "outputs": descriptor["outputs"],
        "portable": descriptor,
        "resources": {"cost_micro_usd": 0},
    }


@pytest.fixture
def observed_inventory(tmp_path, monkeypatch):
    host = tmp_path / "host"
    host.mkdir()
    path = host / "observations.json"
    monkeypatch.setenv("ROUTINEKEEL_HOST_CATALOG", str(path))
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def publish(*records, expired=False):
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "expires_at": (
                        datetime.now(UTC) + timedelta(minutes=-1 if expired else 5)
                    ).isoformat(),
                    "tools": {record["tool"]: record for record in records},
                }
            ),
            encoding="utf-8",
        )
        return workspace

    return publish


@pytest.fixture
def local_csv_metadata(tmp_path, monkeypatch):
    if any(entry.name == "routinekeel.csv_read" for entry in entry_points(group=ADAPTER_GROUP)):
        return
    # Test the real stdlib discovery path when the optional wheel is absent.
    installed = tmp_path / "installed"
    info = installed / "routinekeel_data_adapter-1.0.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(
        "Name: routinekeel-data-adapter\nVersion: 1.0.0\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        "[routinekeel.portable_adapters]\nroutinekeel.csv_read=routinekeel_data_adapter.operations:read_csv\n",
        encoding="utf-8",
    )
    (info / "RECORD").write_text("routinekeel_data_adapter/operations.py,,\n", encoding="utf-8")
    shutil.copytree(
        Path(__file__).resolve().parents[1]
        / "capability_packs/data_adapter/src/routinekeel_data_adapter",
        installed / "routinekeel_data_adapter",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    monkeypatch.syspath_prepend(str(installed))


def test_from_host_to_observed_local_csv_requires_qualification(
    observed_inventory, local_csv_metadata
):
    node = Task.model_validate(csv_node())
    host = adapter("sheets.read")
    local = adapter("routinekeel.csv_read", locus="local_runtime", age=30)
    environment = observed_inventory(host)
    rule = requirement(
        constraints={"max_data_age_seconds": 60, "numeric_representation": "decimal"}
    )
    origin, _ = resolve_requirement(node.id, node, rule, environment)
    target, bindings = resolve_requirement(node.id, node, rule, observed_inventory(local))
    assert origin["candidates"][0]["locus"] == "host_mediated"
    assert target["status"] == "needs_qualification"
    assert target["installation_allowed"] is False
    (choice,) = target["candidates"]
    assert choice["adapter"] == "routinekeel.csv_read@1.0.0"
    assert choice["behavioral_verdict"] == "NOT_RUN"
    assert bindings[choice["adapter"]][1]["provider"] == "portable_local"


@pytest.mark.parametrize(
    ("scenario", "status", "code"),
    [
        ("offline", "needs_installation", "CAPABILITY_UNAVAILABLE"),
        ("expired", "needs_installation", "CAPABILITY_UNAVAILABLE"),
        ("revoked", "needs_permission", "PERMISSION_DENIED"),
        ("binary_float", "needs_installation", "CAPABILITY_UNAVAILABLE"),
        ("too_old", "needs_installation", "CAPABILITY_UNAVAILABLE"),
        ("scope", "needs_installation", "CAPABILITY_UNAVAILABLE"),
        ("missing_pinned", "needs_installation", "PINNED_DEPENDENCY_MISSING"),
        ("optional", "blocked", "OPTIONAL_DECISION_REQUIRED"),
    ],
)
def test_resolution_rejects_wrong_environment_and_intent(
    scenario, status, code, observed_inventory
):
    node = Task.model_validate(csv_node())
    descriptor = adapter(
        "sheets.read",
        allowed=scenario != "revoked",
        network=scenario == "offline",
        age=400 if scenario == "too_old" else 10,
        numeric="binary_float" if scenario == "binary_float" else "decimal",
        permissions=["read"] if scenario == "scope" else [],
    )
    params = {"constraints": {"numeric_representation": "decimal", "max_data_age_seconds": 60}}
    if scenario == "missing_pinned":
        params.update(portability="pinned", pinned_adapter="sheets.original@1.0.0")
    if scenario == "optional":
        params.update(required=False, fallback_contract="tabular.data_omitted")
    environment = observed_inventory(
        *([] if scenario == "optional" else [descriptor]), expired=scenario == "expired"
    )
    result, _ = resolve_requirement(
        node.id, node, requirement(**params), environment, offline=scenario == "offline"
    )
    assert (result["status"], result["code"]) == (status, code)
    assert result["installation_allowed"] is False


def reader_blueprint():
    task = csv_node()
    checked = Task.model_validate(task).model_dump(mode="json", exclude_none=True)
    contract = requirement()
    return {
        "blueprint_schema_version": 2,
        "graph_contract_version": "2.2",
        "workflow": {
            "id": "portable_reader",
            "goal": "讀入表格",
            "revision": 1,
            "version": "1.0.0",
        },
        "inputs": {"csv": PORT},
        "nodes": [task],
        "outputs": {"rows": "artifact://rwp_reader/rows"},
        "quality_contract": {
            "golden_cases": [
                {
                    "inputs": {"csv": "id,amount\nA,0.10\n"},
                    "expected": {"rows": [{"id": "A", "amount": "0.10"}]},
                },
            ]
        },
        "provenance": {
            "pattern_compositions": [
                {
                    "ref": "data.table@1.0.0",
                    "hash": "a" * 64,
                    "pack_ref": "routinekeel.data@1.0.0",
                    "pack_hash": "b" * 64,
                    "qualification_hash": "c" * 64,
                    "decision_id": "d" * 64,
                    "namespace": "table",
                    "members": {"rwp_reader": content_hash(checked)},
                    "requirements": {"rwp_reader": contract.model_dump(mode="json")},
                }
            ]
        },
    }


def test_unqualified_required_candidate_cannot_be_built_or_installed(observed_inventory):
    workspace = observed_inventory(adapter("sheets.read"))
    archive = workspace / "reader.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("design/blueprint.json", json.dumps(reader_blueprint()))
    service = WorkflowService(workspace)
    draft = service.install_workflow(archive=archive)
    assert draft["draft_id"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    state = service.resolve_blueprint_capabilities(draft["draft_id"])
    assert state["status"] == "blocked"
    assert state["portable_decisions"][0]["status"] == "needs_qualification"
    with pytest.raises(FlowError):
        service.install_workflow(
            draft_id=draft["draft_id"], resolution={"capability_fingerprint": state["fingerprint"]}
        )
    assert service.install_workflow(draft_id=draft["draft_id"])["builds"][0]["status"] == "blocked"
    assert not service.workspace.discover(read_only=True)
