import copy
import json
import sqlite3

import pytest
from compiler_helpers import build_ir, runtime_python
from test_discovery import FixtureProvider
from test_verifier import verification_case
from workflow_studio.contracts import StudioError
from workflow_studio.governance.release import Releases
from workflow_studio.llm.usage import Budget
from workflow_studio.registry.builtin import builtin_tools
from workflow_studio.registry.policy import ToolPolicy
from workflow_studio.storage.migrations import APPLICATION_ID, MIGRATIONS, SCHEMA_VERSION
from workflow_studio.storage.sqlite import SQLiteRepository
from workflow_studio.ui.model import StudioUIModel


def custom_copy():
    payload = builtin_tools()[0].model_dump()
    payload.update(tool_id="custom.copy", title="自訂複製")
    return payload


def test_registered_approved_disabled_and_revoked_tool_lifecycle_runs_real_package(tmp_path):
    studio, p, _, cases, verifier, policy = verification_case(tmp_path)
    studio.close()
    model = StudioUIModel(tmp_path / "studio", runtime_python=runtime_python())
    model.select_project(p.project_id)
    registered = model.register_tool(
        custom_copy(), actor="fixture", reason="register reviewed contract"
    )
    assert registered["trust_status"] == "untrusted"
    assert "custom.copy@1.0.0" not in model.editor_tools()
    approved = model.decide_tool(
        registered, "approve", "reviewer", "verified known copy implementation"
    )
    assert "custom.copy@1.0.0" in model.editor_tools()
    node = copy.deepcopy(model.revision().ir["nodes"][0])
    node["tool"] = "custom.copy"
    model.edit_graph(model.project().version, upsert_nodes=[node])
    assert model.verify()["status"] == "passed"
    release = model.approve_publish(tmp_path / "installed", "reviewer", "actual replay passed")
    approval_id = model.approval_id
    manifest = tmp_path / "installed" / release["workflow_id"] / "flow.json"
    original = manifest.read_bytes()
    disabled = model.decide_tool(approved, "disabled", "reviewer", "temporarily stop use")
    with pytest.raises(StudioError):
        model.compile()
    with model.service() as studio:
        with pytest.raises(StudioError):
            Releases(studio).publish(p.project_id, approval_id, tmp_path / "installed", policy)
    assert manifest.read_bytes() == original
    active = model.decide_tool(disabled, "active", "reviewer", "resume same contract")
    assert model.verify()["status"] == "passed"
    model.decide_tool(active, "revoke", "reviewer", "withdraw prior approval")
    assert "custom.copy@1.0.0" not in model.editor_tools()
    history = model.tool_history("custom.copy", "1.0.0")
    assert [event["action"] for event in history["events"]] == [
        "register",
        "approve",
        "disabled",
        "active",
        "revoke",
    ]
    assert len(history["approvals"]) == 1


def test_new_versions_preserve_old_contract_and_versioned_titles(tmp_path):
    model = StudioUIModel(tmp_path)
    first = model.register_tool(custom_copy())
    second = custom_copy()
    second.update(version="1.1.0", title="新版複製")
    model.register_tool(second)
    with pytest.raises(StudioError):
        model.register_tool(second)
    assert model.tool_titles()["custom.copy@1.0.0"] == "自訂複製"
    assert model.tool_titles()["custom.copy@1.1.0"] == "新版複製"
    with model.service() as studio:
        assert studio.tools.get("custom.copy", "1.0.0")["contract_hash"] == first["contract_hash"]
        with pytest.raises(sqlite3.IntegrityError):
            studio.repository.connection.execute("UPDATE studio_tool_events SET action='invented'")
        with pytest.raises(sqlite3.IntegrityError):
            studio.repository.connection.execute("DELETE FROM studio_tool_events")


def test_stale_tool_review_cannot_approve_after_state_changes(tmp_path):
    model = StudioUIModel(tmp_path)
    item = model.register_tool(custom_copy())
    disabled = model.decide_tool(item, "disabled", "fixture", "pause")
    model.decide_tool(disabled, "active", "fixture", "resume")
    with pytest.raises(StudioError) as exc:
        model.decide_tool(item, "approve", "fixture", "stale review")
    assert exc.value.code == "REVISION_CONFLICT"


@pytest.mark.parametrize(
    "raw", ["null", "[]", '{"tool_id":"a","tool_id":"b"}', "not-json", "x" * 128001]
)
def test_tool_import_rejects_ambiguous_or_oversized_json(tmp_path, raw):
    model = StudioUIModel(tmp_path / "studio")
    path = tmp_path / "tool.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(StudioError):
        model.read_tool_file(path)
    assert len(model.tool_records()) == len(builtin_tools())


def test_import_cannot_self_assign_trust_and_unsupported_tools_are_not_advertised(tmp_path):
    studio, p, revision, policy = build_ir(tmp_path)
    source_id = revision.ir["evidence"][0]["source_id"]
    studio.close()
    model = StudioUIModel(tmp_path / "studio")
    model.select_project(p.project_id)
    payload = custom_copy()
    with pytest.raises(StudioError):
        model.register_tool({**payload, "trust_status": "builtin"})
    payload["implementation"] = "unimplemented.external"
    item = model.register_tool(payload)
    model.decide_tool(item, "approve", "reviewer", "metadata approval only")
    record = next(r for r in model.tool_records() if r["tool_id"] == "custom.copy")
    assert record["trust_status"] == "approved" and not record["available"]
    with model.service() as studio:
        planner = studio.discovery(
            FixtureProvider(), Budget(5, 1_000_000, 8000, 1_000_000, 1_000_000), policy
        )
        prepared = planner.prepare(p.project_id, [source_id])
        context = json.loads(json.loads(prepared.messages_json)[1]["content"])
        assert "custom.copy" not in {tool["tool_id"] for tool in context["tools"]}


def test_alias_cannot_understate_real_model_permissions_or_network(tmp_path):
    from test_llm_runtime import llm_ir

    studio, p, revision, _ = build_ir(tmp_path)
    descriptor = next(t for t in builtin_tools() if t.implementation == "builtin.llm").model_dump()
    descriptor.update(tool_id="custom.model", permissions=["workspace.write"], cost_micro_usd=0)
    descriptor["resources"]["network"] = False
    model = StudioUIModel(tmp_path / "studio")
    registered = model.register_tool(descriptor)
    model.decide_tool(registered, "approve", "reviewer", "declared contract is still not authority")
    weak = ToolPolicy("windows", frozenset({"workspace.write"}), False, True, 100_000)
    assert "custom.model@1.0.0" in studio.tools.allowed(weak)
    assert "custom.model@1.0.0" not in studio.tools.available(weak)
    payload = llm_ir()
    payload["revision"] = 2
    payload["evidence"] = revision.ir["evidence"]
    payload["nodes"][0]["tool"] = "custom.model"
    payload["nodes"][0]["resources"]["network"] = False
    candidate = studio.save_ir(
        p.project_id, studio.projects.get(p.project_id).version, payload, weak
    )
    with pytest.raises(StudioError) as exc:
        studio.compile(p.project_id, candidate.revision_id, weak)
    assert exc.value.code == "CONTRACT_MISMATCH"
    studio.close()


@pytest.mark.parametrize("action", ["approve", "disabled"])
def test_audit_failure_rolls_back_tool_change(tmp_path, monkeypatch, action):
    model = StudioUIModel(tmp_path)
    initial = model.register_tool(custom_copy())
    with model.service() as studio:
        catalog = studio.tools

        def fail(*args, **kwargs):
            raise StudioError("STORAGE_INVALID", "audit failure")

        monkeypatch.setattr(catalog, "_event", fail)
        with pytest.raises(StudioError):
            if action == "approve":
                catalog.approve(
                    "custom.copy",
                    "1.0.0",
                    initial["contract_hash"],
                    actor="reviewer",
                    reason="fixture",
                )
            else:
                catalog.set_status("custom.copy", "1.0.0", 1, "disabled")
        assert catalog.get("custom.copy", "1.0.0") == initial
        assert len(catalog.history("custom.copy", "1.0.0")["events"]) == 1
        assert catalog.history("custom.copy", "1.0.0")["approvals"] == []


def test_v010_upgrade_preserves_old_tool_decisions_and_restores_backup(tmp_path):
    root = tmp_path / "v010"
    root.mkdir()
    tool = builtin_tools()[0]
    with sqlite3.connect(root / "studio.sqlite3") as db:
        for version in range(1, 11):
            for statement in MIGRATIONS[version]:
                db.execute(statement)
        db.execute(f"PRAGMA application_id={APPLICATION_ID}")
        db.execute("PRAGMA user_version=10")
        db.execute(
            "INSERT INTO studio_tools VALUES (?,?,?,?,'approved','disabled',3,'before')",
            (tool.tool_id, tool.version, json.dumps(tool.model_dump()), tool.fingerprint),
        )
        db.execute(
            "INSERT INTO studio_tool_approvals(tool_id,version,contract_hash,actor,reason,created_at) VALUES (?,?,?,?,?,'before')",
            (tool.tool_id, tool.version, tool.fingerprint, "legacy reviewer", "legacy decision"),
        )
    repo = SQLiteRepository(root)
    assert repo.schema_version == SCHEMA_VERSION
    event = repo.connection.execute("SELECT * FROM studio_tool_events").fetchone()
    assert event["action"] == "snapshot" and event["actor"] is None
    assert json.loads(event["payload_json"])["status"] == "disabled"
    assert (
        repo.connection.execute("SELECT actor FROM studio_tool_approvals").fetchone()[0]
        == "legacy reviewer"
    )
    backup = next((root / "backups").glob("before-v010-*.sqlite3"))
    repo.close()
    restored = SQLiteRepository.restore(backup, tmp_path / "restored")
    with sqlite3.connect(restored) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 10
        assert db.execute("SELECT status FROM studio_tools").fetchone()[0] == "disabled"
        assert not db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='studio_tool_events'"
        ).fetchone()


def test_repair_does_not_send_tool_audit_paths_to_provider(tmp_path):
    from test_model_repair import setup
    from workflow_studio.registry.models import ToolDescription

    studio, p, _, cases, repair, old = setup(tmp_path)
    description = custom_copy()
    description["test_evidence"] = ["C:/private/provider-diagnostics.log"]
    tool = ToolDescription.model_validate(description)
    studio.tools.register(tool)
    studio.tools.approve(
        tool.tool_id, tool.version, tool.fingerprint, actor="fixture", reason="local review"
    )
    prepared = repair.prepare(p.project_id, old.report_id, cases)
    assert (
        list(repair.run(prepared, authorized_hash=prepared.request_hash))[-1]["status"] == "passed"
    )
    assert "provider-diagnostics" not in json.dumps(repair.provider.requests)
    assert "test_evidence" not in repair.provider.requests[0]["tools"]["custom.copy@1.0.0"]
    studio.close()


def test_discovery_receives_capabilities_and_cost_without_audit_paths(tmp_path):
    studio, p, revision, policy = build_ir(tmp_path)
    from workflow_studio.registry.models import ToolDescription

    description = custom_copy()
    description["test_evidence"] = ["C:/private/tool-proof.txt"]
    tool = ToolDescription.model_validate(description)
    studio.tools.register(tool)
    studio.tools.approve(
        tool.tool_id, tool.version, tool.fingerprint, actor="reviewer", reason="known contract"
    )
    planner = studio.discovery(
        FixtureProvider(), Budget(5, 1_000_000, 8000, 1_000_000, 1_000_000), policy
    )
    prepared = planner.prepare(p.project_id, [revision.ir["evidence"][0]["source_id"]])
    context = json.loads(json.loads(prepared.messages_json)[1]["content"])
    candidate = next(t for t in context["tools"] if t["tool_id"] == "custom.copy")
    assert candidate["capabilities"] == ["file.copy"]
    assert candidate["permissions"] == ["workspace.write"]
    assert candidate["cost_micro_usd"] == 0
    assert "tool-proof.txt" not in prepared.messages_json
    studio.close()
