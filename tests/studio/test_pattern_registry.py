import hashlib
import json
import sqlite3
import subprocess
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from flow_base.core.service import WorkflowService
from routinekeel_contracts.asset_loading import load_asset
from routinekeel_contracts.domain_assets import canonical_json, content_hash
from workflow_studio.contracts import StudioError
from workflow_studio.registry.packs import CatalogDecision
from workflow_studio.service import StudioService
from workflow_studio.storage import sqlite as storage

PACK = "routinekeel.text@1.0.0"
PATTERN = "text.preserve@1.0.0"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/domain_assets"


def make_pack(
    path,
    *,
    pack_id="routinekeel.text",
    version="1.0.0",
    dependencies=(),
    pattern_version="1.0.0",
    changed=False,
):
    pattern = (
        (FIXTURES / "pattern.yaml")
        .read_bytes()
        .replace(b"  version: 1.0.0", f"  version: {pattern_version}".encode(), 1)
    )
    if changed:
        pattern = pattern.replace("保留原文".encode(), "保留字元".encode())
    assets = {
        "capabilities/copy.yaml": (FIXTURES / "capability.yaml").read_bytes(),
        "patterns/preserve.yaml": pattern,
        "cases/index.json": b'{"schema_version":1,"cases":[]}',
    }
    manifest = {
        "pack_schema_version": "1.0",
        "pack": {
            "id": pack_id,
            "version": version,
            "title": "Text",
            "domains": ["text"],
            "publisher": "local",
            "runtime_compatibility": ">=0.3,<0.4",
        },
        "capability_refs": ["text.copy@1.0.0"],
        "pattern_refs": [f"text.preserve@{pattern_version}"],
        "dependencies": list(dependencies),
        "provenance": {"source": "test fixture", "license": "MIT"},
        "assets": {name: hashlib.sha256(raw).hexdigest() for name, raw in assets.items()},
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("pack.json", json.dumps(manifest))
        for name, raw in assets.items():
            archive.writestr(name, raw)
    return path


def decision(operation, item, *, evidence=None):
    return CatalogDecision(
        operation,
        item["ref"],
        item.get("manifest_hash") or item["content_hash"],
        item["revision"],
        "registry-test",
        "simulated local decision",
        "local_terminal",
        evidence,
    )


def install_previous_workflow(workspace):
    graph = load_asset("pattern", FIXTURES / "pattern.yaml").model.graph.model_dump(mode="json")
    # Installed pre-v1.3 workflows refer to the existing implementation version.
    graph["nodes"][0]["capability_version"] = "1"
    graph.update(
        blueprint_schema_version=2,
        workflow={"id": "workflow", "version": "1.0.0", "goal": "保留原文", "revision": 1},
        provenance={},
        quality_contract={"golden_cases": [{"inputs": {"text": "ok"}, "expected": {"text": "ok"}}]},
    )
    path = workspace / "old-workflow.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("design/blueprint.json", json.dumps(graph))
    service = WorkflowService(workspace)
    draft_id = service.install_workflow(archive=path)["draft_id"]
    report = service.resolve_blueprint_capabilities(draft_id)
    candidate = service.install_workflow(
        draft_id=draft_id, resolution={"capability_fingerprint": report["fingerprint"]}
    )
    installed = service.install_workflow(
        draft_id=draft_id, build_id=candidate["build_id"], install_hash=candidate["install_hash"]
    )
    assert installed["status"] == "installed"
    return service, draft_id


@pytest.fixture
def catalog(tmp_path):
    source = make_pack(tmp_path / "domain.zip")
    with StudioService.open_workspace(tmp_path / "workspace") as studio:
        yield studio.tools.packs, source


def test_register_is_idempotent_hash_bound_and_transactional(catalog, tmp_path):
    registry, source = catalog
    item = registry.register(source)
    assert item["status"] == "candidate"
    assert item["trust_status"] == "pending_approval"
    assert registry.register(source) == item
    assert registry.archive_bytes(PACK) == source.read_bytes()
    assert [event["action"] for event in registry.history(PACK)] == ["register"]
    assert registry.patterns() == []
    assert registry.patterns(include_inactive=True)[0]["ref"] == PATTERN
    assert registry.by_capability("text.copy")[0]["pattern_ref"] == PATTERN
    assert registry.pattern_pack_refs(PATTERN) == [PACK]
    with pytest.raises(StudioError, match="此 Pack 版本已存在"):
        registry.register(make_pack(tmp_path / "changed.zip", changed=True))
    with pytest.raises(StudioError, match="資產版本已存在"):
        registry.register(make_pack(tmp_path / "invalid.zip", version="1.1.0", changed=True))
    assert registry.list() == [item]
    assert registry.archive_bytes(PACK) == source.read_bytes()


def test_pack_approval_does_not_grant_pattern_approval(catalog):
    registry, source = catalog
    pending = registry.register(source)
    with pytest.raises(StudioError, match="可信"):
        registry.decide({"actor": "approved", "reason": "payload", "ref": PACK})
    result = registry.decide(decision("approve", pending))
    assert result["status"] == "active"
    assert registry.require_available(PACK, "0.3.0") == result
    with pytest.raises(StudioError, match="相容"):
        registry.require_available(PACK, "0.4.0")
    item = registry.pattern_status(PATTERN)
    assert item["status"] == "candidate" and registry.patterns() == []
    with pytest.raises(StudioError, match="試做收據"):
        registry.decide_pattern(decision("approve", item, evidence="0" * 64))
    assert registry.pattern_status(PATTERN) == item


def test_concurrent_transition_persists_one_decision(catalog):
    registry, source = catalog
    item = registry.register(source)
    state_root = registry.repository.root
    barrier = Barrier(2)

    def apply():
        with StudioService(storage.SQLiteRepository(state_root)) as studio:
            barrier.wait(15)
            try:
                return studio.tools.packs.decide(decision("approve", item))["status"]
            except StudioError as error:
                return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: apply(), range(2))) == ["REVISION_CONFLICT", "active"]
    events = registry.history(PACK)
    assert [event["action"] for event in events] == ["register", "approve"]
    assert events[1]["review_channel"] == "local_terminal"
    assert events[1]["decision_hash"] == decision("approve", item).fingerprint


def test_disabling_or_revoking_preserves_existing_pin_and_blocks_new_adoption(catalog, tmp_path):
    registry, source = catalog
    pack = registry.decide(decision("approve", registry.register(source)))
    item = registry.pattern_status(PATTERN)
    # Seed a simulated verifier receipt to exercise the Registry gate independently.
    report = {
        "verdict": "PASS",
        "simulated": True,
        "fixture": "registry lifecycle",
        "pattern_ref": PATTERN,
        "pattern_hash": item["content_hash"],
        "pack_ref": PACK,
        "pack_hash": pack["manifest_hash"],
        "quality_report": {"verdict": "PASS"},
        "conformance_report": {"builder_receipt": {"simulated": True}},
    }
    digest = content_hash(report)
    registry.repository.connection.execute(
        "INSERT INTO studio_pattern_trials VALUES (?,?,?,?,'PASS')",
        (PATTERN, item["content_hash"], digest, canonical_json(report)),
    )
    approved = registry.decide_pattern(decision("approve", item, evidence=digest))
    assert approved["status"] == "active"
    assert registry.patterns(domain="text") == [approved]
    assert registry.patterns(domain="data") == []
    service, blueprint_hash = install_previous_workflow(tmp_path / "workspace")
    locked = registry.pin_workflow("workflow@1.0.0", blueprint_hash, PATTERN, PACK, "0.3.0")
    assert registry.dependents(PATTERN) == [
        {
            "workflow_ref": "workflow@1.0.0",
            "blueprint_hash": blueprint_hash,
            "pattern_hash": locked.sha256,
            "pack_ref": PACK,
        }
    ]
    disabled = registry.decide(decision("disable", pack))
    assert registry.patterns() == []
    assert registry.pin_workflow("workflow@1.0.0", blueprint_hash, PATTERN, PACK, "0.3.0") == locked
    source_file = tmp_path / "workspace/after-disable.json"
    source_file.write_text('{"inputs":{"text":"kept after disable"}}', encoding="utf-8")
    result = service.run_workflow_sync({"task": "workflow", "source": source_file})
    assert result["blueprint"]["outputs"] == {"text": "kept after disable"}
    with pytest.raises(StudioError, match="新建置"):
        registry.pin_workflow("workflow@1.0.1", "2" * 64, PATTERN, PACK, "0.3.0")
    revoked = registry.decide(decision("revoke", disabled))
    assert revoked["trust_status"] == "pending_approval"
    archived = registry.decide(decision("archive", revoked))
    assert archived["status"] == "archived"
    assert registry.list() == []
    assert registry.archive_bytes(PACK) == source.read_bytes()
    registry.register(make_pack(tmp_path / "later.zip", version="1.1.0", pattern_version="1.1.0"))
    assert registry.get_asset("pattern", PATTERN) == locked
    assert registry.dependents(PATTERN)[0]["pattern_hash"] == locked.sha256
    assert registry.check_update(PATTERN) == []


def test_pack_dependency_index_and_disable(catalog, tmp_path):
    registry, source = catalog
    root = registry.register(source)
    downstream = registry.register(
        make_pack(tmp_path / "dependent.zip", pack_id="other.text", dependencies=[PACK])
    )
    assert registry.dependent_packs(PACK) == ["other.text@1.0.0"]
    with pytest.raises(StudioError, match="未核准"):
        registry.decide(decision("approve", downstream))
    active = registry.decide(decision("approve", root))
    registry.decide(decision("approve", downstream))
    assert registry.require_available(downstream["ref"], "0.3.0")["status"] == "active"
    registry.decide(decision("disable", active))
    with pytest.raises(StudioError, match="停用或未核准"):
        registry.require_available(downstream["ref"], "0.3.0")


def test_v15_backup_can_restore_in_previous_schema(tmp_path, monkeypatch):
    root = tmp_path / "previous"
    with monkeypatch.context() as patch:
        patch.setattr(storage, "SCHEMA_VERSION", 15)
        with StudioService.open_workspace(root) as studio:
            project = studio.projects.create("既有工作", {"target": "keep"})
            interview = studio.interviews.start(project.project_id, project.version)
    with StudioService.open_workspace(root) as migrated:
        assert migrated.repository.schema_version == storage.SCHEMA_VERSION
        assert migrated.interviews.get(project.project_id) == interview
        registry = migrated.tools.packs
        registry.register(make_pack(tmp_path / "pack.zip"))
        assert (
            migrated.repository.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        )
        (backup,) = migrated.repository.path.parent.glob("backups/before-v015-*.sqlite3")
    restored_root = tmp_path / "restored"
    restored_path = storage.SQLiteRepository.restore(backup, restored_root)
    with sqlite3.connect(restored_path.as_uri() + "?mode=ro", uri=True) as restored:
        assert restored.execute("PRAGMA user_version").fetchone() == (15,)
        assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert not restored.execute(
            "SELECT name FROM sqlite_master WHERE name='studio_domain_packs'"
        ).fetchall()
    with monkeypatch.context() as patch:
        patch.setattr(storage, "SCHEMA_VERSION", 15)
        with StudioService(storage.SQLiteRepository(restored_root)) as studio:
            assert studio.interviews.get(project.project_id) == interview


def test_cli_catalog_is_durable_and_noninteractive_approval_fails(tmp_path):
    path = make_pack(tmp_path / "pack.zip")
    workspace = tmp_path / "workspace"

    def call(operation, payload):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "workflow_studio",
                "--workspace",
                str(workspace),
                "pack",
                operation,
                "--payload",
                json.dumps(payload),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=30,
        )
        return result.returncode, json.loads(result.stdout)

    code, answer = call("register", {"path": str(path)})
    assert code == 0 and answer["result"]["status"] == "candidate"
    assert call("get", {"ref": PACK}) == (0, answer)
    code, forbidden = call("approve", {"ref": PACK, "actor": "caller"})
    assert code == 2 and forbidden["error"]["code"] == "PATTERN_APPROVAL_REQUIRED"
