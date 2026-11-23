import hashlib
import json
import stat
import zipfile
from importlib.resources import files
from pathlib import Path

import pytest
from flow_base.core.service import WorkflowService
from flow_base.runtime import FlowError
from jsonschema import Draft202012Validator
from routinekeel_contracts.asset_loading import (
    load_asset,
    load_asset_bytes,
    load_pack,
)
from routinekeel_contracts.domain_assets import asset_schemas
from routinekeel_contracts.errors import StudioError

FIXTURES = Path(__file__).parent / "fixtures/domain_assets"


def pack_files():
    assets = {
        "capabilities/copy.yaml": (FIXTURES / "capability.yaml").read_bytes(),
        "patterns/preserve.yaml": (FIXTURES / "pattern.yaml").read_bytes(),
        "policies/source.yaml": (FIXTURES / "policy.yaml").read_bytes(),
        "profile_schemas/preferences.yaml": (FIXTURES / "profile_schema.yaml").read_bytes(),
        "cases/index.json": b'{"schema_version":1,"cases":[]}',
    }
    manifest = {
        "pack_schema_version": "1.0",
        "pack": {
            "id": "routinekeel.text",
            "version": "1.0.0",
            "title": "Text Pack",
            "domains": ["text"],
            "publisher": "local-user",
            "runtime_compatibility": ">=0.3,<0.4",
        },
        "capability_refs": ["text.copy@1.0.0"],
        "pattern_refs": ["text.preserve@1.0.0"],
        "policy_refs": ["text.source_immutability@1.0.0"],
        "profile_schema_refs": ["text.delivery_preferences@1.0.0"],
        "provenance": {"source": "RoutineKeel test fixture", "license": "MIT"},
        "assets": {path: hashlib.sha256(raw).hexdigest() for path, raw in assets.items()},
    }
    return {"pack.json": json.dumps(manifest).encode(), **assets}


def write_pack(path, entries):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as output:
        for name, data in entries.items():
            output.writestr(name, data)
    return path


@pytest.mark.parametrize("kind", ["pack", "capability", "pattern", "policy", "profile_schema"])
def test_schema_fixtures_roundtrip_and_canonical_hash(kind):
    snapshot = json.loads(
        files("routinekeel_contracts").joinpath("domain_assets.schema.json").read_bytes()
    )
    assert snapshot == asset_schemas()
    if kind == "pack":
        raw = pack_files()["pack.json"]
        first = load_asset_bytes(kind, raw)
        second = load_asset_bytes(kind, raw, ".yaml")  # JSON is a YAML subset.
    else:
        raw = (FIXTURES / f"{kind}.yaml").read_bytes()
        first = load_asset(kind, FIXTURES / f"{kind}.yaml")
        second = load_asset_bytes(kind, first.canonical)
    Draft202012Validator(snapshot[kind]).validate(json.loads(first.canonical))
    assert second.canonical == first.canonical
    assert second.sha256 == first.sha256 == hashlib.sha256(first.canonical).hexdigest()
    assert first.source_sha256 == hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize(
    ("operation", "yaml_value"),
    [
        ("pattern_schema_version: '1.0'", "pattern_schema_version: '1.1'"),
        ("pattern_schema_version: '1.0'", "pattern_schema_version: 1.0"),
        ("  trust_status", "  approved"),
        ("  title: 保留原文", "  title: 保留原文\n  trust_status: active"),
        ("  title: 保留原文", "  title: 保留原文\n  unexpected: true"),
        ("  title: 保留原文", "  title: 保留原文\n  title: duplicate"),
        ("  title: 保留原文", "  title: &title 保留原文"),
        ("  title: 保留原文", "  title: !!str 保留原文"),
        ("1.0.0", "01.0.0"),
        ("artifact://copy/result", "artifact://absent/result"),
        ("kind: task", "kind: python"),
        ("permissions: []", "permissions: []\n      parameters: {shell: whoami}"),
        ("permissions: []", "permissions: []\n      depends_on: [copy]"),
        ("    required: true", "    required: false\n    id: copier"),
    ],
)
def test_reject_inert_asset_faults(operation, yaml_value):
    raw = (FIXTURES / "pattern.yaml").read_text(encoding="utf-8")
    if operation == "  trust_status":
        raw = raw.replace("  title: 保留原文", "  title: 保留原文\n  trust_status: candidate")
    modified = raw.replace(operation, yaml_value, 1)
    assert modified != raw
    with pytest.raises(StudioError):
        load_asset_bytes("pattern", modified.encode(), ".yaml")


def test_reject_duplicate_requirement_id_and_json_field():
    pattern = load_asset("pattern", FIXTURES / "pattern.yaml").model.model_dump(mode="json")
    pattern["requirements"].append(dict(pattern["requirements"][0]))
    with pytest.raises(StudioError):
        load_asset_bytes("pattern", json.dumps(pattern).encode())
    with pytest.raises(StudioError):
        load_asset_bytes("pack", b'{"pack_schema_version":"1.0","pack_schema_version":"1.0"}')


def test_pack_inventory_and_exact_source_identity(tmp_path):
    entries = pack_files()
    path = write_pack(tmp_path / "pack.zip", entries)
    pack = load_pack(path)
    assert pack.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert pack.manifest.model.pack.ref == "routinekeel.text@1.0.0"
    assert len(pack.definitions) == 4
    entries["cases/index.json"] = b"{}"
    with pytest.raises(StudioError, match="指紋"):
        load_pack(write_pack(path, entries))


@pytest.mark.parametrize("fault", ["symlink", "script", "traversal", "version", "trust", "case"])
def test_pack_rejects_unsafe_members(tmp_path, fault):
    entries = pack_files()
    manifest = json.loads(entries["pack.json"])
    if fault == "symlink":
        name = zipfile.ZipInfo("cases/index.json")
        name.create_system = 3
        name.external_attr = (stat.S_IFLNK | 0o777) << 16
        entries[name] = entries.pop("cases/index.json")
    elif fault == "version":
        manifest["pattern_refs"] = ["text.preserve@1.0.1"]
    elif fault == "trust":
        manifest["pack"]["trust"] = "approved"
    else:
        name = {"script": "cases/test.py", "traversal": "../notes.txt", "case": "cases/INDEX.json"}[
            fault
        ]
        entries[name] = b"{}"
        manifest["assets"][name] = hashlib.sha256(b"{}").hexdigest()
    entries["pack.json"] = json.dumps(manifest).encode()
    with pytest.raises(StudioError):
        load_pack(write_pack(tmp_path / "pack.zip", entries))


def test_pattern_cannot_be_installed_as_blueprint_or_selected_for_runtime(tmp_path):
    asset = load_asset("pattern", FIXTURES / "pattern.yaml")
    archive = write_pack(tmp_path / "pattern.zip", {"design/blueprint.json": asset.canonical})
    service = WorkflowService(tmp_path)
    with pytest.raises(FlowError):
        service.install_workflow(archive=archive)
    with pytest.raises(FlowError):
        service.submit_workflow({"task": asset.model.pattern.ref, "source": archive})
    assert not service.workspace.discover(read_only=True)
