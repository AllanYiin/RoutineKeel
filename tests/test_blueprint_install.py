"""First install and host continuation using the public Service and real wheel loader."""

import json
import sys
import zipfile
from pathlib import Path

import pytest
from flow_base.blueprints.source import inspect_blueprint
from flow_base.core.service import WorkflowService
from flow_base.runtime import FlowError

pytestmark = pytest.mark.usefixtures("desktop_test_catalog")


def blueprint(path, version="1.0.0", review=True):
    steps = [
        {"id": "1", "title": "Draft", "inputs": ["text"], "outputs": ["draft"]},
        {"id": "2", "title": "Send", "inputs": ["draft"], "outputs": ["receipt"]},
    ]
    manifest = {
        "schema_version": 1,
        "runtime_api": "0.2",
        "id": "example_flow",
        "version": version,
        "title": "Example",
        "description": "Local test",
        "keywords": ["example"],
    }
    definition = {
        "definition_version": "0.2",
        "execution": {"order": "linear"},
        "workflow_id": manifest["id"],
        "steps": steps,
        "final_outputs": {"items": [{"name": "receipt", "from": "2"}]},
        "review": {
            "points": [{"after_step": "1", "shows": ["draft"]}] if review else []
        },
    }
    return write_source(path, manifest, definition)


def write_source(path, manifest, definition):
    rows = definition["steps"]
    stages = {s["id"]: {k: s[k] for k in ("inputs", "outputs")} for s in rows}
    names = {p for s in rows for p in [*s["inputs"], *s["outputs"]]}
    docs = {
        "flow.json": manifest,
        "workflow.definition.json": definition,
        "contracts.json": {
            "stages": stages,
            "artifacts": {
                p: {"schema": {"type": "string", "minLength": 1}} for p in names
            },
        },
        "dependencies.json": {"items": []},
        "skill-map.json": {"steps": {s["id"]: [] for s in rows}, "skills": []},
        "gaps.json": {"deterministic_code": [], "mcp_calls": []},
        "task-config.example.json": {},
        "glossary.json": {},
    }
    with zipfile.ZipFile(path, "w") as z:
        for name, data in docs.items():
            z.writestr("sample/" + name, json.dumps(data))
    return path


def resolution():
    program = 'def transform(text, params):\n    return json_dumps({"draft": json_loads(text)["inputs"]["text"].upper()})'
    return {
        "steps": {
            "1": {
                "mode": "local",
                "result": program,
                "examples": [
                    {
                        "context": {"inputs": {"text": v}},
                        "expected": {"draft": v.upper()},
                    }
                    for v in ("a", "b")
                ],
            },
            "2": {"mode": "external", "capability": "post"},
        },
        "bindings": {
            "post": {
                "kind": "host_probe",
                "tool": "test-sink",
                "version": "1",
                "mapping": {"draft": "body"},
                "evidence": "local test sink receipt",
                "input_schema": {"type": "object", "required": ["body"]},
                "output_schema": {"type": "object", "required": ["receipt"]},
                "probe": {
                    "tool": "test-sink",
                    "version": "1",
                    "status": "passed",
                    "request_id": "test-1",
                    "observed": "sent",
                },
            }
        },
        "decisions": {"contracts": "confirmed for test"},
        "gaps": {},
    }


def case():
    return {
        "inputs": {"text": "hello"},
        "expected": {"receipt": "sent"},
        "replies": [
            {"step": "1", "kind": "review"},
            {"step": "2", "kind": "external", "outputs": {"receipt": "sent"}},
        ],
    }


@pytest.mark.parametrize("review_at", [None, "4"], ids=["automatic", "review-delivery"])
def test_four_local_steps_pause_only_at_declared_review(tmp_path, review_at):
    service = WorkflowService(tmp_path)
    steps, recipes = [], {}
    for i in range(1, 5):
        source, output = f"p{i-1}", f"p{i}"
        steps.append({"id":str(i),"title":f"轉換 {i}","inputs":[source],"outputs":[output]})
        recipes[str(i)] = {"mode":"local",
            "result":f'def transform(text, params):\n    return json_dumps({{"{output}": json_loads(text)["inputs"]["{source}"]}})',
            "examples":[{"context":{"inputs":{source:value}},"expected":{output:value}} for value in ("A","B")]}
    points = [{"after_step":"4","shows":["p4"]}] if review_at else []
    definition = {"definition_version":"0.2","workflow_id":"four_steps","execution":{"order":"linear"},
                  "steps":steps,"review":{"points":points},"final_outputs":{"items":[{"name":"p4","from":"4"}]}}
    manifest = {"schema_version":1,"runtime_api":"0.2","id":"four_steps","title":"四步轉換",
                "description":"測試本機自動接續","keywords":["四步"],"version":"1.0.0"}
    blueprint_file = write_source(tmp_path/"four.zip",manifest,definition)
    draft = service.install_workflow(archive=blueprint_file)["draft_id"]
    graph = service.install_workflow(draft_id=draft)["source"]["graph"]
    assert sum(node["kind"] == "human_review" for node in graph["nodes"]) == len(points)
    replies = [{"step":"4","kind":"review"}] if review_at else []
    trial = service.install_workflow(draft_id=draft,
        resolution={"steps":recipes,"bindings":{},"gaps":{},"decisions":{"contracts":"測試合約"}},
        cases=[{"inputs":{"p0":"四步完成"},"expected":{"p4":"四步完成"},"replies":replies}])
    assert trial["evidence"]["cases"][0]["simulated_host_and_review"] == replies


def candidate(ws, version="1.0.0"):
    service = WorkflowService(ws)
    path = blueprint(ws / "attachment.zip", version)
    draft = service.install_workflow(archive=path)["draft_id"]
    built = service.install_workflow(
        draft_id=draft, resolution=resolution(), cases=[case()]
    )
    return service, draft, built


def apply(service, draft, built):
    return service.install_workflow(
        draft_id=draft, build_id=built["build_id"], install_hash=built["install_hash"]
    )


@pytest.mark.parametrize("missing", [True, False], ids=["not-run", "validator-exception"])
def test_required_validation_blocks_build_and_preserves_draft(tmp_path, monkeypatch, missing):
    from flow_base.blueprints.installer import draft_dir
    from routinekeel_contracts import validator

    service = WorkflowService(tmp_path)
    draft = service.install_workflow(archive=blueprint(tmp_path / "input.zip"))["draft_id"]
    if missing:
        monkeypatch.setitem(sys.modules, "routinekeel_contracts.validator", None)
    else:
        def unavailable(*args):
            raise RuntimeError("validator failed")
        monkeypatch.setattr(validator, "validate_ir", unavailable)
    with pytest.raises(FlowError):
        service.install_workflow(draft_id=draft, resolution=resolution(), cases=[case()])
    records = list(draft_dir(service.workspace, draft).glob("builds/*/verification.json"))
    assert len(records) == 1
    receipt = json.loads(records[0].read_bytes())
    assert receipt["status"] == "blocked"
    assert receipt["validation"]["status"] == ("NOT_RUN" if missing else "FAIL")
    assert "install_hash" not in receipt
    assert not service.workspace.discover(read_only=True)


def test_install_requires_current_validator_even_after_passing_trial(tmp_path, monkeypatch):
    service, draft, built = candidate(tmp_path)
    assert built["validation"] == {"policy_version": "blueprint-ir-record/1", "status": "PASS"}
    monkeypatch.setitem(sys.modules, "routinekeel_contracts.validator", None)
    with pytest.raises(FlowError):
        apply(service, draft, built)
    assert not service.workspace.discover(read_only=True)


def finish(service, result, op, **data):
    pending=result["pending"]
    if op=="complete" and pending["kind"] in {"human","review"} and data.get("evidence",{}).get("decision")=="approved":
        data.update(scope_hash=pending["scope_hash"],payload_hash=pending["payload_hash"])
        data["evidence"]={**data["evidence"],"user_text":"核對本次顯示的內容及對外範圍後同意"}
    if op=="complete" and pending.get("claimed") and data.get("evidence",{}).get("receipt"):
        from flow_base.graph.external import record_observation
        record_observation(service.workspace.root,pending["idempotency_key"],pending["scope_hash"],
                           data["evidence"]["receipt"],"succeeded",data["outputs"])
    return service.blueprint_task(
        result["session_id"],
        op,
        {"task_id": result["pending"]["id"], "revision": result["revision"], **data},
    )


def test_install_run_review_revise_reconnect_and_publish_once(tmp_path):
    service, draft, built = candidate(tmp_path)
    assert built["evidence"]["provider_calls"] == 0
    assert not service.workspace.discover(read_only=True)
    assert apply(service, draft, built)["status"] == "installed"
    assert apply(service, draft, built)["status"] == "already_installed"
    source = tmp_path / "input.json"
    source.write_text(json.dumps({"inputs": {"text": "hello"}}))
    result = service.run_workflow_sync({"task": "example_flow", "source": source})[
        "blueprint"
    ]
    assert result["pending"]["kind"] == "review"
    retried = finish(service, result, "revise", step="1", reason="重新產生")
    with pytest.raises(FlowError):
        finish(
            service,
            result,
            "complete",
            evidence={"actor": "reviewer", "decision": "approved"},
        )
    assert retried["pending"]["id"] != result["pending"]["id"]
    approved = finish(
        WorkflowService(tmp_path),
        retried,
        "complete",
        evidence={"actor": "reviewer", "decision": "approved"},
    )
    assert approved["pending"]["kind"] == "external"
    with pytest.raises(FlowError):
        finish(service, approved, "complete", outputs={"receipt": "sent"})
    claimed = finish(service, approved, "claim")
    assert WorkflowService(tmp_path).blueprint_task(claimed["session_id"])["pending"][
        "claimed"
    ]
    with pytest.raises(FlowError):
        finish(service, claimed, "claim")
    with pytest.raises(FlowError):
        finish(service, claimed, "revise", step="1", reason="late")
    done = finish(
        service,
        claimed,
        "complete",
        outputs={"receipt": "sent"},
        evidence={"receipt": "id123"},
    )
    assert done["outputs"] == {"receipt": "sent"}
    assert (Path(done["output"]) / "artifacts/results.json").is_file()


def test_failed_build_stays_draft_and_case_cannot_activate(tmp_path):
    service = WorkflowService(tmp_path)
    draft = service.install_workflow(archive=blueprint(tmp_path / "a.zip"))["draft_id"]
    bad = case()
    bad["expected"]["receipt"] = "untrue"
    with pytest.raises(FlowError):
        service.install_workflow(draft_id=draft, resolution=resolution(), cases=[bad])
    failed = service.install_workflow(draft_id=draft)["builds"][0]
    assert failed["status"] == "blocked" and "不符" in failed["reason"]
    assert not service.workspace.discover()
    fake = resolution()
    fake["bindings"]["post"]["kind"] = "fixture"
    built = service.install_workflow(draft_id=draft, resolution=fake, cases=[case()])
    with pytest.raises(FlowError):
        apply(service, draft, built)


def test_later_attachment_uses_existing_update_and_portable_reimport(tmp_path):
    service, draft, old = candidate(tmp_path)
    apply(service, draft, old)
    service, draft, new = candidate(tmp_path, "1.0.1")
    assert apply(service, draft, new)["status"] == "update_required"
    plan = service.preview_workflow_update(new["archive"])
    service.update_workflow(new["archive"], plan_hash=plan["plan_hash"])
    assert apply(service, draft, new)["status"] == "already_installed"
    assert service.install_workflow(archive=new["archive"])["draft_id"] == draft


def test_source_never_executes_embedded_code_and_rejects_unsafe_paths(tmp_path):
    path = blueprint(tmp_path / "bp.zip")
    with zipfile.ZipFile(path, "a") as z:
        z.writestr("../bad.py", "print(1)")
    with pytest.raises(FlowError):
        inspect_blueprint(path.read_bytes())
