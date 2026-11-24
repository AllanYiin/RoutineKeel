import json
import os
import sqlite3
import zipfile
from pathlib import Path

import pytest
from flow_base.core.service import WorkflowService
from flow_base.graph.locking import read_lock
from flow_base.graph.qualification import BindingReview
from flow_base.runtime import FlowError
from routinekeel_contracts.domain_assets import content_hash
from routinekeel_contracts.graph import Task
from test_capability_resolver import adapter, observed_inventory, reader_blueprint  # noqa: F401


def echo_blueprint():
    graph = reader_blueprint()
    node = graph["nodes"][0]
    node.update(
        capability="text.copy",
        inputs={
            "source": {
                "source": "input://csv",
                "port": {"type": "string"},
            }
        },
        outputs={"result": {"type": "string"}},
    )
    graph["outputs"] = {"text": "artifact://rwp_reader/result"}
    composition = graph["provenance"]["pattern_compositions"][0]
    composition["members"][node["id"]] = content_hash(
        Task.model_validate(node).model_dump(mode="json", exclude_none=True)
    )
    composition["requirements"][node["id"]]["capability"] = "text.copy"
    graph["quality_contract"] = {
        "golden_cases": [{"inputs": {"csv": "Observed"}, "expected": {"text": "Observed"}}],
        "adapter_cases": {
            "read": [
                {"id": "latin", "inputs": {"source": "Known"}, "expected": {"result": "Known"}},
                {"id": "cjk", "inputs": {"source": "保留"}, "expected": {"result": "保留"}},
            ]
        },
    }
    return graph


def draft(workspace, graph):
    path = workspace / "blueprint.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("design/blueprint.json", json.dumps(graph))
    service = WorkflowService(workspace)
    return service, service.install_workflow(archive=path)["draft_id"]


def qualify(service, key, choices):
    scope = service.preview_binding_qualification(key, choices)
    return service.qualify_blueprint_bindings(
        key, choices, BindingReview(scope["request_hash"], "test operator response")
    )


def verify_install(service, draft_id):
    resolution = service.resolve_blueprint_capabilities(draft_id, offline=True)
    assert resolution["status"] == "ready", resolution
    candidate = service.install_workflow(
        draft_id=draft_id, resolution={"capability_fingerprint": resolution["fingerprint"]}
    )
    assert candidate["status"] == "verified"
    installed = service.install_workflow(
        draft_id=draft_id, build_id=candidate["build_id"], install_hash=candidate["install_hash"]
    )
    assert installed["status"] == "installed"
    return candidate


def test_qualify_lock_build_install_and_run_local_adapter(tmp_path, monkeypatch):
    # This test isolates capability qualification with synthetic Pattern catalog hashes.
    monkeypatch.setattr("flow_base.graph.builder.verify_pattern_admission", lambda *args: None)
    service, draft_id = draft(tmp_path, echo_blueprint())
    choices = {"rwp_reader": "routinekeel.text_copy@1.0.0"}
    preview = service.preview_binding_qualification(draft_id, choices)
    assert preview["local_only"] and len(preview["case_hashes"]["rwp_reader"]) == 64
    with pytest.raises(FlowError, match="確認"):
        service.qualify_blueprint_bindings(
            draft_id, choices, {"request_hash": preview["request_hash"]}
        )
    result = qualify(service, draft_id, choices)
    assert result["status"] == "qualified", result
    receipt = result["reports"]["rwp_reader"]
    assert receipt["conformance"]["status"] == receipt["behavior"]["status"] == "PASS"
    lock = result["lock"]
    verified, evidence = read_lock(tmp_path, lock["graph_hash"])
    assert verified.lock_hash == lock["lock_hash"]
    assert evidence["rwp_reader"] == receipt
    path = service.workspace.runtime.home / "portable_locks" / (lock["lock_hash"] + ".json")
    assert all(secret not in path.read_text(encoding="utf-8") for secret in ("Known", "保留"))
    candidate = verify_install(service, draft_id)
    assert candidate["capability_evidence"]["rwp_reader"]["lock_hash"] == verified.lock_hash
    request = tmp_path / "input.json"
    request.write_text('{"inputs":{"csv":"hello"}}')
    run = service.run_workflow_sync({"task": "portable_reader", "source": request})
    assert run["blueprint"]["outputs"] == {"text": "hello"}
    tampered = json.loads(path.read_bytes())
    tampered["workflow"] = "another@1.0.0"
    path.write_text(json.dumps(tampered), encoding="utf-8")
    state = service.resolve_blueprint_capabilities(draft_id)
    assert state["portable_decisions"][0]["code"] == "BINDING_STALE"
    with pytest.raises(FlowError, match="綁定"):
        service.install_workflow(
            draft_id=draft_id,
            build_id=candidate["build_id"],
            install_hash=candidate["install_hash"],
        )


@pytest.mark.parametrize("fault", ["NOT_RUN", "FAIL"])
def test_failed_required_qualification_cannot_write_current_lock(tmp_path, fault):
    graph = echo_blueprint()
    if fault == "NOT_RUN":
        graph["quality_contract"].pop("adapter_cases")
    else:
        graph["quality_contract"]["adapter_cases"]["read"][1]["expected"]["result"] = "wrong"
    service, key = draft(tmp_path, graph)
    result = qualify(service, key, {"rwp_reader": "routinekeel.text_copy@1.0.0"})
    assert result["status"] == "blocked" and result["lock"] is None
    result = result["reports"]["rwp_reader"]
    assert result["verdict"] == fault
    assert read_lock(tmp_path, result["graph_hash"]) is None
    assert not service.workspace.discover(read_only=True)


def test_host_model_probe_not_run_without_real_authorized_result(observed_inventory):  # noqa: F811
    host_tool = adapter("provider.model")
    host_tool["portable"]["model_version"] = "alternative-model-b"
    service, key = draft(observed_inventory(host_tool), reader_blueprint())
    report = qualify(service, key, {"rwp_reader": "provider.model@1.0.0"})
    assert (
        report["reports"]["rwp_reader"]["conformance"]["code"]
        == "AUTHORIZED_HOST_QUALIFICATION_REQUIRED"
    )
    assert report["reports"]["rwp_reader"]["behavior"]["status"] == "NOT_RUN"
    assert report["lock"] is None
    assert not service.workspace.discover(read_only=True)


def host_observation(report, cases, model, *, wrong=False):
    return {
        "observation": "executed",
        "request_hash": report["request_hash"],
        "environment_fingerprint": report["adapters"]["rwp_reader"]["environment_fingerprint"],
        "case_hash": report["case_hashes"]["rwp_reader"],
        "model_version": model,
        "authorization_hash": "f" * 64,
        "conformance": {
            key: "PASS"
            for key in ("inputs", "outputs", "permissions", "timeout", "side_effects", "budget")
        },
        "results": [
            {
                "id": case["id"],
                "input_hash": content_hash(case["inputs"]),
                "output_hash": "0" * 64 if wrong else content_hash(case["expected"]),
                "cost_micro_usd": 0,
            }
            for case in cases
        ],
    }


def test_host_data_with_separate_qualification_waits_when_disconnected(
    observed_inventory,  # noqa: F811
    monkeypatch,
):
    monkeypatch.setattr("flow_base.graph.builder.verify_pattern_admission", lambda *args: None)
    graph = reader_blueprint()
    cases = [
        {
            "id": "invoice",
            "inputs": {"source": "id,amount\nA,0.10"},
            "expected": {"rows": [{"id": "A", "amount": "0.10"}]},
        },
        {
            "id": "different",
            "inputs": {"source": "id,amount\nB,0.25"},
            "expected": {"rows": [{"id": "B", "amount": "0.25"}]},
        },
    ]
    graph["quality_contract"]["adapter_cases"] = {"read": cases}
    graph["quality_contract"]["golden_cases"][0]["replies"] = [
        {
            "step": "rwp_reader",
            "kind": "host",
            "outputs": {"rows": [{"id": "A", "amount": "0.10"}]},
        },
    ]
    tool = adapter("provider.model")
    tool["portable"]["model_version"] = "B"
    workspace = observed_inventory(tool)
    service, key = draft(workspace, graph)
    choices = {"rwp_reader": "provider.model@1.0.0"}
    proposal = service.preview_binding_qualification(key, choices)
    # Publish fixture observations in the trusted-launcher location, with no provider calls.
    tool["portable_qualification"] = host_observation(proposal, cases, "B", wrong=True)
    observed_inventory(tool)
    wrong = qualify(service, key, choices)["reports"]["rwp_reader"]
    assert wrong["conformance"]["status"] == "PASS" and wrong["behavior"]["status"] == "FAIL"
    assert not service.workspace.discover(read_only=True)
    tool["portable_qualification"] = host_observation(proposal, cases, "B")
    observed_inventory(tool)
    assert qualify(service, key, choices)["status"] == "qualified"
    verify_install(service, key)
    name = os.environ["ROUTINEKEEL_HOST_CATALOG"]
    observed_inventory()
    request = workspace / "waiting.json"
    request.write_text(json.dumps({"inputs": {"csv": "id,amount\nA,0.10"}}))
    result = service.run_workflow_sync({"task": "portable_reader", "source": request})
    pending = result["blueprint"]["pending"]
    assert pending["kind"] == "host" and pending["scope"]["tool"] == "provider.model"
    session_id = result["blueprint"]["session_id"]
    assert service.blueprint_task(session_id, "status")["pending"]["id"] == pending["id"]
    blocked = service.blueprint_task(
        session_id,
        "claim",
        {
            "task_id": pending["id"],
            "revision": result["blueprint"]["revision"],
            "scope_hash": pending["scope_hash"],
            "payload_hash": pending["payload_hash"],
        },
    )
    assert blocked["code"] == "REBIND_REQUIRED" and "dispatch" not in blocked
    assert blocked["pending"]["id"] == pending["id"]
    observed_inventory(tool)
    monkeypatch.setenv("ROUTINEKEEL_HOST_CATALOG", name)
    resumed = service.blueprint_task(
        session_id,
        "claim",
        {
            "task_id": pending["id"],
            "revision": blocked["revision"],
            "scope_hash": pending["scope_hash"],
            "payload_hash": pending["payload_hash"],
        },
    )
    assert resumed["pending"]["id"] == pending["id"]
    tool["portable"]["model_version"] = "C"
    observed_inventory(tool)
    assert (
        service.resolve_blueprint_capabilities(key)["portable_decisions"][0]["code"]
        == "BINDING_STALE"
    )


def test_optional_fallback_requires_new_and_approved_delivery(tmp_path):
    original = reader_blueprint()
    original["provenance"]["pattern_compositions"][0]["requirements"]["rwp_reader"].update(
        required=False,
        fallback_contract="tabular.data_omitted",
    )
    original["inputs"]["subject"] = {"type": "string"}
    original["nodes"].append({
        "id": "subject", "kind": "task", "capability": "text.copy", "capability_version": "1",
        "inputs": {"text": {"source": "input://subject", "port": {"type": "string"}}},
        "outputs": {"text": {"type": "string"}},
        "permissions": [], "side_effect": "none",
    })
    original["outputs"]["subject"] = "artifact://subject/text"
    original["quality_contract"]["golden_cases"][0]["inputs"]["subject"] = "OK"
    original["quality_contract"]["golden_cases"][0]["expected"]["subject"] = "OK"
    original["quality_contract"]["execution_review"] = {
        "mode": "stop_on_error", "scope": "保留標題",
        "critical_steps": {"subject": {"machine": [{
            "id": "same", "output": "text", "evaluator": "equals", "expected": "OK",
        }]}},
    }
    service, origin = draft(tmp_path, original)
    variant = {
        "blueprint_schema_version": 2,
        "workflow": {
            "id": "portable_reader",
            "version": "1.1.0",
            "revision": 2,
            "goal": "交付空列",
        },
        "graph_contract_version": "2.2",
        "inputs": {"fallback": {"type": "array"}},
        "nodes": [
            {
                "id": "deliver",
                "kind": "task",
                "capability": "json.identity",
                "capability_version": "1",
                "inputs": {"value": {"source": "input://fallback", "port": {"type": "array"}}},
                "outputs": {"rows": {"type": "array"}},
                "permissions": [],
                "side_effect": "none",
            }
        ],
        "outputs": {"rows": "artifact://deliver/rows"},
        "provenance": {
            "degraded_delivery": {
                "source_hash": origin,
                "omissions": {"rwp_reader": "tabular.data_omitted"},
            }
        },
        "quality_contract": {
            "golden_cases": [{"inputs": {"fallback": []}, "expected": {"rows": []}}]
        },
    }
    variant["inputs"]["subject"] = original["inputs"]["subject"]
    variant["nodes"].append(original["nodes"][-1])
    variant["outputs"]["subject"] = original["outputs"]["subject"]
    variant["quality_contract"]["golden_cases"][0]["inputs"]["subject"] = "OK"
    variant["quality_contract"]["golden_cases"][0]["expected"]["subject"] = "OK"
    service, weakened = draft(tmp_path, variant)
    with pytest.raises(FlowError, match="保留其餘步驟"):
        service.preview_blueprint_degradation(origin, weakened)
    variant["quality_contract"]["execution_review"] = original["quality_contract"]["execution_review"]
    service, candidate = draft(tmp_path, variant)
    resolution = service.resolve_blueprint_capabilities(candidate)
    request = {"capability_fingerprint": resolution["fingerprint"]}
    with pytest.raises(FlowError):
        service.install_workflow(draft_id=candidate, resolution=request)
    option = service.preview_blueprint_degradation(origin, candidate)
    with pytest.raises(FlowError):
        service.approve_blueprint_degradation(origin, candidate, BindingReview("wrong", "reply"))
    granted = service.approve_blueprint_degradation(
        origin,
        candidate,
        BindingReview(option["request_hash"], "simulated reviewed alternate"),
    )
    assert granted["status"] == "degraded_candidate"
    assert granted["optional_disabled"] == ["rwp_reader"]
    assert service.install_workflow(draft_id=candidate, resolution=request)["status"] == "verified"
    assert service.resolve_blueprint_capabilities(origin)["status"] == "blocked"
    assert not service.workspace.discover(read_only=True)


def test_v005_binding_migration_keeps_a_verified_restore_point(tmp_path, monkeypatch):
    import hashlib

    from flow_base.storage import migrations

    service = WorkflowService(tmp_path)
    runtime = service.workspace.runtime
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS[:5])
        runtime.initialize()
    fresh = WorkflowService(tmp_path).workspace.runtime
    fresh.initialize()
    backup = fresh.home / "state.sqlite3.before-v006.sqlite3"
    assert (
        hashlib.sha256(backup.read_bytes()).hexdigest()
        == Path(str(backup) + ".sha256").read_text(encoding="ascii").strip()
    )
    restored = sqlite3.connect(tmp_path / "restored.sqlite3")
    with sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True) as original:
        original.backup(restored)
    assert restored.execute("PRAGMA user_version").fetchone()[0] == 5
    assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    restored.close()
