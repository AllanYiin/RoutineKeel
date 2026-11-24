import json
import time
import zipfile

import pytest
from flow_base.core.service import WorkflowService
from flow_base.runtime import FlowError
from test_binding_qualification import draft, echo_blueprint
from test_capability_resolver import adapter, observed_inventory, reader_blueprint  # noqa: F401
from test_graph_builder import data_graph
from test_workspace import cli


def gap_archive(path):
    graph = data_graph()
    graph["nodes"][0]["capability"] = "unknown-tool"
    graph["quality_contract"]["golden_cases"] = ["將第二份文字轉為大寫"]
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("graph/blueprint.json", json.dumps(graph))
    return path


def wait_completion(
    service, state, targets=("verified", "installed", "needs_input", "needs_update")
):
    end = time.monotonic() + 45
    while time.monotonic() < end:
        status = service.blueprint_completion("status", state["work_id"])
        if status["status"] in targets:
            return status
        time.sleep(0.15)
    logs = service.workspace.runtime.logs / "blueprint-completion" / state["work_id"]
    pytest.fail(
        f"補強未走到 {targets}: {status}\n"
        + "\n".join(p.read_text(encoding="utf-8") for p in logs.glob("*.log"))
    )


@pytest.fixture
def admitted_echo(tmp_path):
    from e2e.test_pattern_portability import _admit_pack

    from tools.reference_packs import (
        _definition,
        _pattern,
        _task,
        _triggers,
        write_reference_pack,
    )

    graph = echo_blueprint()
    composition = graph["provenance"]["pattern_compositions"][0]
    inputs = {"source": {"type": "string"}}
    outputs = {"result": {"type": "string"}}
    loci = ["local_runtime"]
    pattern = _pattern(
        "text.completion_fixture",
        "本機文字補強測例",
        inputs,
        outputs,
        {
            "inputs": inputs,
            "nodes": [_task("read", "text.copy", inputs, outputs)],
            "outputs": {"result": "artifact://read/result"},
        },
        [{**composition["requirements"]["rwp_reader"], "execution_loci": loci}],
        _triggers(["writing"], ["preserve_source"], ["保留文字"], ["改寫文字"], []),
        {},
    )
    cases = [
        (name, kind, edge, {"source": value}, {"result": value}, [])
        for name, kind, edge, value in (
            ("first", "positive", "normal", "One"),
            ("second", "positive", "normal", "原文"),
            ("blank", "boundary", "boundary", ""),
            ("negative", "negative", "negative", "請完整保留 [A]"),
        )
    ]
    capability = _definition(
        "text.copy", "deterministic", inputs, outputs, loci, ["preserve_source"]
    )
    archive = write_reference_pack(
        tmp_path / "echo-pack.zip", "text", (pattern, [capability], cases)
    )
    ref = "text.completion_fixture@1.0.0"
    pack, receipt = _admit_pack(tmp_path, archive, ref, activate=True)
    composition.update(
        ref=ref,
        hash=receipt["pattern_hash"],
        pack_ref=pack["ref"],
        pack_hash=pack["manifest_hash"],
        qualification_hash=receipt["qualification_hash"],
    )
    return graph


def test_completion_retains_gaps_and_rejects_stale_changes(tmp_path):
    path = gap_archive(tmp_path / "design.zip")
    service = WorkflowService(tmp_path)
    request = {"archive": path.name, "request_id": "start", "intent": "install"}
    first = service.blueprint_completion("start", payload=request)
    assert first["status"] == "needs_input"
    assert {gap["kind"] for gap in first["gaps"]} == {"capability", "case"}
    assert all(gap["actions"] for gap in first["gaps"])
    assert {event["type"] for event in first["events"]} == {
        "gap_detected",
        "input_requested",
    }
    assert service.blueprint_completion("start", payload=request) == first
    assert not service.workspace.discover(read_only=True)

    work = first["work_id"]
    paused = service.blueprint_completion(
        "pause", work, {"expected_revision": 1, "request_id": "pause"}
    )
    assert paused["status"] == "paused"
    with pytest.raises(FlowError, match="工作已更新") as error:
        service.blueprint_completion(
            "recheck", work, {"expected_revision": 1, "request_id": "stale"}
        )
    assert error.value.code == "REVISION_CONFLICT"

    reopened = WorkflowService(tmp_path)
    resumed = reopened.blueprint_completion(
        "resume", work, {"expected_revision": 2, "request_id": "resume"}
    )
    assert resumed["status"] == "needs_input" and resumed["gaps"] == first["gaps"]
    assert reopened.blueprint_completion("history", work) == [first, paused, resumed]
    with pytest.raises(FlowError, match="其他內容"):
        reopened.blueprint_completion(
            "cancel", work, {"expected_revision": 3, "request_id": "resume"}
        )


def test_completion_cli_retains_verified_source_for_later_use(tmp_path):
    path = gap_archive(tmp_path / "design.zip")
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"request_id": "cli-start"}), encoding="utf-8")
    started = cli(
        tmp_path,
        "blueprint-completion",
        "start",
        "--archive",
        path,
        "--payload",
        request,
    )["result"]
    reopened = cli(
        tmp_path, "blueprint-completion", "status", "--work-id", started["work_id"]
    )["result"]
    assert reopened == started and started["source_draft_id"] == started["draft_id"]
    assert (
        cli(tmp_path, "install-workflow", "--draft-id", started["draft_id"])["result"][
            "builds"
        ]
        == []
    )


def test_completion_qualifies_a_pure_adapter_while_cases_wait(tmp_path, monkeypatch):
    graph = echo_blueprint()
    graph["quality_contract"]["golden_cases"] = ["保留輸入文字"]
    service, key = draft(tmp_path, graph)
    started = service.blueprint_completion(
        "start", payload={"draft_id": key, "request_id": "automatic"}
    )
    assert started["status"] == "needs_input"
    assert {gap["kind"] for gap in started["gaps"]} == {"builder", "case"}
    assert any("review_pattern_admission" in gap["actions"] for gap in started["gaps"])
    assert started["environment_lock"]["blueprint_hash"] == key
    assert started["qualification"]["rwp_reader"]["report"]["verdict"] == "PASS"
    history = service.blueprint_completion("history", started["work_id"])
    assert [event["status"] for event in history] == ["qualifying", "needs_input"]

    def unexpected_probe(*args, **kwargs):
        pytest.fail("unchanged proof must be reused")

    monkeypatch.setattr("flow_base.graph.qualification._measure", unexpected_probe)
    reopened = WorkflowService(tmp_path).blueprint_completion(
        "recheck",
        started["work_id"],
        {"request_id": "fresh", "expected_revision": started["revision"]},
    )
    assert reopened["qualification"] == started["qualification"]
    assert reopened["environment_lock"] == started["environment_lock"]


def test_missing_adapter_examples_are_an_actionable_gap(tmp_path, admitted_echo):
    graph = admitted_echo
    graph["quality_contract"]["adapter_cases"] = {}
    service, key = draft(tmp_path, graph)
    started = service.blueprint_completion(
        "start", payload={"draft_id": key, "request_id": "adapter-cases"}
    )
    expected = next(item for item in started["gaps"] if item["kind"] == "capability")
    assert (
        expected["qualification_report"]["conformance"]["code"]
        == "TRIAL_CASES_REQUIRED"
    )
    assert "complete_adapter_cases" in expected["actions"]
    assert started["environment_lock"] is None
    supplied = service.blueprint_completion(
        "submit",
        started["work_id"],
        {
            "request_id": "qualification-suite",
            "expected_revision": started["revision"],
            "action": "complete_adapter_cases",
            "gap_id": expected["id"],
            "scope_hash": expected["scope_hash"],
            "user_text": "保留這兩個已知字串",
            "cases": echo_blueprint()["quality_contract"]["adapter_cases"]["read"],
        },
    )
    supplied = wait_completion(service, supplied)
    assert supplied["status"] == "verified"
    assert supplied["environment_lock"]["bindings"]["rwp_reader"][
        "qualification_receipt"
    ]


def test_described_case_can_be_completed_without_revising_source(tmp_path):
    graph = data_graph()
    graph["quality_contract"]["golden_cases"] = ["保留原文並驗證大寫"]
    service, key = draft(tmp_path, graph)
    source = service.install_workflow(draft_id=key)["source"]
    pending = service.blueprint_completion(
        "start", payload={"draft_id": key, "request_id": "described"}
    )
    gap = pending["gaps"][0]
    assert gap["intent"] == "保留原文並驗證大寫" and gap["draft"]["needs_confirmation"]
    assert gap["draft"]["expected"]["upper"] == gap["draft"]["inputs"]["text"].upper()
    payload = {
        "request_id": "confirmed-case",
        "expected_revision": pending["revision"],
        "action": "complete_case",
        "gap_id": gap["id"],
        "scope_hash": gap["scope_hash"],
        "user_text": "核對兩項輸出。",
        "case": {
            "inputs": {"text": "Mix"},
            "expected": {"original": "Mix", "upper": "MIX"},
        },
    }
    completed = service.blueprint_completion("submit", pending["work_id"], payload)
    completed = wait_completion(service, completed)
    assert completed["status"] == "verified"
    assert completed["case_coverage"][0]["case"] == payload["case"]
    assert completed["case_coverage"][0]["intent"] == gap["intent"]
    assert completed["draft_id"] == key
    assert service.install_workflow(draft_id=key)["source"] == source


def test_trial_progress_sharing_conflict_keeps_status_available(tmp_path, monkeypatch):
    from flow_base.blueprints import completion_delivery, trial

    def locked(path, *_args):
        assert path.name == "trial-progress.json"
        raise PermissionError("fixture sharing violation")

    monkeypatch.setattr(completion_delivery, "read_json", locked)
    monkeypatch.setattr(completion_delivery, "worker_alive", lambda _state: True)
    completion = {"draft_id": "a" * 64, "trial": {"build_id": "b" * 32}}
    service = WorkflowService(tmp_path)
    assert completion_delivery.trial_progress(service, completion) == {
        "progress_available": False,
        "worker_alive": True,
    }
    monkeypatch.setattr(trial, "atomic_json", locked)
    trial._write_progress(tmp_path / "trial-progress.json", {"case": 1, "phase": "prepare"})


def test_blueprint_assertions_supply_independent_expected_values(tmp_path):
    graph = data_graph()
    graph["quality_contract"] = {
        "golden_cases": ["核對轉換前後"],
        "case_recipes": {
            "1": {
                "inputs": {"text": "MiX"},
                "assertions": {
                    "original": {"input": "text", "operation": "identity"},
                    "upper": {"input": "text", "operation": "uppercase"},
                },
            }
        },
    }
    service, key = draft(tmp_path, graph)
    ready = service.blueprint_completion(
        "start", payload={"draft_id": key, "request_id": "automatic-case"}
    )
    ready = wait_completion(service, ready)
    assert ready["status"] == "verified"
    assert ready["case_coverage"] == [
        {
            "location": "case/1",
            "intent": "核對轉換前後",
            "evidence": "blueprint_assertions",
            "case": {
                "inputs": {"text": "MiX"},
                "expected": {"original": "MiX", "upper": "MIX"},
            },
        }
    ]


def test_attach_fixture_copies_local_input_and_saves_preview_metadata(tmp_path):
    service_path = tmp_path / "workspace"
    service_path.mkdir()
    service = WorkflowService(service_path)
    source = service_path / "sample.txt"
    source.write_text("example", encoding="utf-8")
    external = tmp_path / "outside.txt"
    external.write_text("outside", encoding="utf-8")
    blueprint = gap_archive(service_path / "design.zip")
    pending = service.blueprint_completion(
        "start", payload={"archive": blueprint.name, "request_id": "fixtures"}
    )
    payload = {
        "request_id": "attach",
        "expected_revision": pending["revision"],
        "action": "attach_fixture",
    }
    with pytest.raises(FlowError, match="工作區"):
        service.blueprint_completion(
            "submit", pending["work_id"], {**payload, "source": str(external)}
        )
    accepted = service.blueprint_completion(
        "submit", pending["work_id"], {**payload, "source": source.name}
    )
    fixture = next(iter(accepted["fixtures"].values()))
    assert fixture["media_type"] == "text/plain" and fixture["size"] == 7
    source.write_text("changed", encoding="utf-8")
    assert (service_path / fixture["reference"]["$file"]).read_text(
        encoding="utf-8"
    ) == "example"


def test_completion_adopts_a_confirmed_studio_revision(
    tmp_path, workflow_design_review
):
    from flow_base.studio import studio_request

    original = data_graph()
    original["nodes"][0]["capability"] = "unregistered"
    service, old_key = draft(tmp_path, original)
    pending = service.blueprint_completion(
        "start", payload={"draft_id": old_key, "request_id": "old"}
    )
    graph = data_graph()
    graph["workflow"]["revision"] = 2
    graph["workflow"]["version"] = "1.0.1"
    graph["quality_contract"]["success_criteria"] = ["文字一致"]
    project = studio_request(
        tmp_path, "create", {"name": "文字", "task": "驗證相容替代工具"}
    )
    pid = project["project_id"]
    proposal = studio_request(
        tmp_path,
        "prepare_blueprint",
        {
            "project_id": pid,
            "expected_revision": project["revision"],
            "request_id": "preview",
            "graph": graph,
            "design": {"rules": "保留原文"},
            "lineage": {"base_source_hash": old_key, "improvement_id": "b" * 64},
        },
    )
    final = studio_request(
        tmp_path,
        "finalize_workflow_design",
        {
            "project_id": pid,
            "expected_revision": proposal["revision"],
            "request_id": "finalize",
            "draft_id": proposal["proposal_id"],
            "review": workflow_design_review(proposal),
            "draft_reply": "初稿可進入定稿",
            "shown_sections": {
                key: item["sha256"] for key, item in proposal["review_sections"].items()
            },
        },
    )
    exported = studio_request(
        tmp_path,
        "export_blueprint",
        {
            "project_id": pid,
            "expected_revision": final["revision"],
            "request_id": "export",
            "proposal_id": final["proposal_id"],
            "review_hash": final["review_hash"],
            "shown_sections": {
                key: item["sha256"] for key, item in final["review_sections"].items()
            },
            "decision": "approve",
            "user_text": "確認定稿並匯出",
        },
    )
    revised = service.install_workflow(archive=exported["path"])["draft_id"]
    preview = service.blueprint_completion(
        "preview_revision", pending["work_id"], {"draft_id": revised}
    )
    assert preview["changes"]["nodes"]["to"] and preview["source"] == old_key
    payload = {
        "request_id": "adopt",
        "expected_revision": pending["revision"],
        "action": "adopt_revision",
        "draft_id": revised,
        "user_text": "採用新版工具",
    }
    with pytest.raises(FlowError, match="實際差異"):
        service.blueprint_completion(
            "submit", pending["work_id"], {**payload, "scope_hash": "wrong"}
        )
    accepted = service.blueprint_completion(
        "submit",
        pending["work_id"],
        {
            **payload,
            "scope_hash": preview["scope_hash"],
        },
    )
    assert accepted["source_draft_id"] == old_key and accepted["draft_id"] == revised
    accepted = wait_completion(service, accepted)
    assert accepted["status"] == "verified"


def test_auto_completion_qualifies_builds_trials_installs_and_runs(
    tmp_path, admitted_echo
):
    graph = admitted_echo
    graph["quality_contract"].update(
        golden_cases=["保留輸入文字"],
        case_recipes={
            "1": {
                "inputs": {"csv": "實際驗收"},
                "assertions": {"text": {"input": "csv", "operation": "identity"}},
            }
        },
    )
    service, key = draft(tmp_path, graph)
    started = service.blueprint_completion(
        "start",
        payload={
            "draft_id": key,
            "request_id": "install-with-completion",
            "intent": "install",
        },
    )
    assert started["status"] == "trialing"
    reopened = WorkflowService(tmp_path)
    installed = wait_completion(reopened, started, ("installed", "needs_input"))
    assert installed["status"] == "installed", installed
    assert installed["environment_lock"]["blueprint_hash"] == key
    assert installed["candidate"]["validation"]["status"] == "PASS"
    assert installed["candidate"]["completion_cases"]["coverage_hash"]
    assert installed["candidate"]["evidence"]["cases"][0]["run_id"]
    assert installed["installation"]["workflow_id"] == "portable_reader"
    events = reopened.blueprint_completion("history", started["work_id"])
    kinds = {event["type"] for revision in events for event in revision["events"]}
    assert {"action_verified", "trial_finished", "install_rechecked"} <= kinds
    request = tmp_path / "input.json"
    request.write_text(
        json.dumps({"inputs": {"csv": "續做後執行"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    assert reopened.run_workflow_sync(
        {
            "task": "portable_reader",
            "source": request,
        }
    )["blueprint"]["outputs"] == {"text": "續做後執行"}


def test_trial_failure_returns_a_scoped_repair_action_and_resumes(tmp_path):
    graph = data_graph()
    graph["quality_contract"]["golden_cases"] = ["核對大寫輸出"]
    service, key = draft(tmp_path, graph)
    first = service.blueprint_completion(
        "start", payload={"draft_id": key, "request_id": "repair-trial"}
    )

    def complete(state, request_id, expected):
        gap = state["gaps"][0]
        return service.blueprint_completion(
            "submit",
            state["work_id"],
            {
                "request_id": request_id,
                "expected_revision": state["revision"],
                "action": "complete_case",
                "gap_id": gap["id"],
                "scope_hash": gap["scope_hash"],
                "user_text": "已核對合約與參考輸出。",
                "case": {
                    "inputs": {"text": "ab"},
                    "expected": {"original": "ab", "upper": expected},
                },
            },
        )

    failure = wait_completion(service, complete(first, "before-fix", "wrong"))
    assert failure["status"] == "needs_input"
    assert failure["gaps"][0]["location"] == "case/1"
    assert failure["gaps"][0]["phase"] == "compare"
    assert not service.workspace.discover(read_only=True)
    fixed = wait_completion(service, complete(failure, "after-fix", "AB"))
    assert fixed["status"] == "verified" and fixed["draft_id"] == key
    assert fixed["candidate"]["build_id"] != failure["trial"]["build_id"]


def test_invalid_source_is_replaceable_in_same_job(tmp_path):
    broken = tmp_path / "broken.zip"
    broken.write_text("bad zip", encoding="utf-8")
    service = WorkflowService(tmp_path)
    initial = service.blueprint_completion(
        "start", payload={"archive": broken.name, "request_id": "bad-source"}
    )
    assert initial["status"] == "needs_source" and initial["draft_id"] is None
    new = gap_archive(tmp_path / "replacement.zip")
    repaired = service.blueprint_completion(
        "submit",
        initial["work_id"],
        {
            "request_id": "replace-file",
            "expected_revision": initial["revision"],
            "action": "replace_source",
            "scope_hash": initial["gaps"][0]["scope_hash"],
            "archive": new.name,
        },
    )
    assert repaired["status"] == "needs_input"
    assert repaired["draft_id"] == repaired["source_draft_id"]


def test_dead_trial_worker_can_resume_same_reserved_attempt(tmp_path, monkeypatch):
    from types import SimpleNamespace

    service, key = draft(tmp_path, data_graph())
    with monkeypatch.context() as fault:
        fault.setattr(
            "flow_base.blueprints.completion_delivery.subprocess.Popen",
            lambda *args, **kwargs: SimpleNamespace(pid=2**30),
        )
        first = service.blueprint_completion(
            "start", payload={"draft_id": key, "request_id": "interrupted"}
        )
    assert not service.blueprint_completion("status", first["work_id"])[
        "trial_progress"
    ]["worker_alive"]
    resumed = service.blueprint_completion(
        "advance",
        first["work_id"],
        {
            "request_id": "continue-interrupted",
            "expected_revision": first["revision"],
        },
    )
    assert resumed["trial"]["build_id"] == first["trial"]["build_id"]
    assert wait_completion(service, resumed)["status"] == "verified"


def test_repeated_worker_loss_becomes_a_resumable_gap(tmp_path, monkeypatch):
    from types import SimpleNamespace

    service, key = draft(tmp_path, data_graph())
    monkeypatch.setattr(
        "flow_base.blueprints.completion_delivery.subprocess.Popen",
        lambda *args, **kwargs: SimpleNamespace(pid=2**30),
    )
    state = service.blueprint_completion(
        "start", payload={"draft_id": key, "request_id": "lost"}
    )
    for number in range(3):
        state = service.blueprint_completion(
            "advance",
            state["work_id"],
            {
                "request_id": f"recover-{number}",
                "expected_revision": state["revision"],
            },
        )
    assert state["status"] == "needs_input"
    assert state["gaps"][0]["kind"] == "trial"
    assert "retry_trial" in state["gaps"][0]["actions"]
    assert state["trial"]["launch_attempts"] == 3


def test_confirmed_case_accepts_the_requested_file_and_trials(tmp_path):
    graph = data_graph()
    graph["nodes"] = [graph["nodes"][1]]
    graph["outputs"] = {"original": "artifact://copy/result"}
    graph["quality_contract"]["golden_cases"] = [
        {
            "inputs": {"text": {"$file": "fixtures/required.txt"}},
            "expected": {"original": {"$artifact": True}},
        }
    ]
    service, key = draft(tmp_path, graph)
    pending = service.blueprint_completion(
        "start", payload={"draft_id": key, "request_id": "required-fixture"}
    )
    assert pending["gaps"][0]["actions"] == ["attach_fixture", "revise_blueprint"]
    (tmp_path / "selected.txt").write_text("fixture content", encoding="utf-8")
    gap = pending["gaps"][0]
    queued = service.blueprint_completion(
        "submit",
        pending["work_id"],
        {
            "request_id": "use-requested-fixture",
            "expected_revision": pending["revision"],
            "action": "attach_fixture",
            "gap_id": gap["id"],
            "scope_hash": gap["scope_hash"],
            "source": "selected.txt",
            "requested_path": "fixtures/required.txt",
        },
    )
    assert (tmp_path / "fixtures" / "required.txt").read_text(
        encoding="utf-8"
    ) == "fixture content"
    assert wait_completion(service, queued)["status"] == "verified"


def test_declining_a_host_adapter_keeps_independent_case_work(request):
    inventory = request.getfixturevalue("observed_inventory")
    workspace = inventory(adapter("host-csv"))
    graph = reader_blueprint()
    graph["quality_contract"]["golden_cases"] = ["核對表格輸出"]
    service, key = draft(workspace, graph)
    initial = service.blueprint_completion(
        "start", payload={"draft_id": key, "request_id": "host-denial"}
    )
    gap = next(row for row in initial["gaps"] if row["kind"] == "capability")
    choice = gap["candidates"][0]["adapter"]
    denied = service.blueprint_completion(
        "submit",
        initial["work_id"],
        {
            "request_id": "decline-host",
            "expected_revision": initial["revision"],
            "action": "decline_adapter",
            "gap_id": gap["id"],
            "scope_hash": gap["scope_hash"],
            "adapter": choice,
            "user_text": "我拒絕這個宿主工具。",
        },
    )
    assert denied["declined_adapters"][gap["location"]][choice]["user_text"]
    case_gap = next(row for row in denied["gaps"] if row["kind"] == "case")
    supplied = service.blueprint_completion(
        "submit",
        denied["work_id"],
        {
            "request_id": "independent-case",
            "expected_revision": denied["revision"],
            "action": "complete_case",
            "gap_id": case_gap["id"],
            "scope_hash": case_gap["scope_hash"],
            "user_text": "確認案例",
            "case": {"inputs": {"csv": "a\n1"}, "expected": {"rows": [{"a": "1"}]}},
        },
    )
    assert {row["kind"] for row in supplied["gaps"]} == {"builder", "capability"}
    assert supplied["gaps"][0]["candidates"][0]["declined"]
    assert not supplied.get("qualification")
