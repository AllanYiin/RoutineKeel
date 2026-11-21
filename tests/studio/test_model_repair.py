import copy
import json

import pytest
from ir_cases import valid_ir
from test_verifier import verification_case
from workflow_studio.compiler.generator import Compiler
from workflow_studio.contracts import StudioError
from workflow_studio.llm.provider import Chunk
from workflow_studio.llm.usage import Budget
from workflow_studio.service import StudioService
from workflow_studio.verifier.model_repair import ModelRepair


class RepairProvider:
    destination = "https://fixture.invalid/repair"
    model = "repair-fixture"
    external = True

    def __init__(self, transform=None, *, interrupted=False, malformed=False):
        self.transform = transform
        self.interrupted, self.malformed = interrupted, malformed
        self.calls, self.requests = 0, []

    def stream(self, messages, max_output_tokens):
        self.calls += 1
        content = json.loads(messages[1]["content"])
        self.requests.append(content)
        ir = copy.deepcopy(content["current_ir"])
        ir["nodes"][0]["postconditions"] = [
            {"operator": "exists", "reference": "artifact://copy/result"}
        ]
        response = {"analysis": "修正錯誤的指紋條件，保持成功案例不變。", "ir": ir, "questions": []}
        if self.transform:
            self.transform(response, content, self.calls)
        raw = "not json" if self.malformed else json.dumps(response, ensure_ascii=False)
        yield Chunk(text=raw[:40])
        if self.interrupted:
            raise ConnectionError("private-provider-detail-must-not-leak")
        yield Chunk(text=raw[40:])
        yield Chunk(usage={"prompt_tokens": 50, "completion_tokens": 100})
        yield Chunk(done=True)


def setup(tmp_path, provider=None):
    payload = valid_ir()
    payload["nodes"][0]["postconditions"] = [
        {"operator": "sha256", "reference": "artifact://copy/result", "value": "0" * 64}
    ]
    studio, project, build, cases, verifier, policy = verification_case(tmp_path, payload=payload)
    report = verifier.verify(project.project_id, build["build_id"], cases, policy)
    assert report["status"] == "failed"
    provider = provider or RepairProvider()
    repair = ModelRepair(
        studio,
        provider,
        Budget(5, 1_000_000, 8000, 1_000_000, 1_000_000),
        policy,
        runtime_python=verifier.runtime_python,
    )
    prepared = repair.prepare(project.project_id, report["report_id"], cases)
    return studio, project, build, cases, repair, prepared


def test_scoped_model_repair_streams_then_compiles_and_replays(tmp_path):
    studio, p, build, cases, repair, prepared = setup(tmp_path)
    events = list(repair.run(prepared, authorized_hash=prepared.request_hash))
    assert events[0]["event"] == "repair.started"
    assert sum(e["event"] == "repair.delta" for e in events) == 2
    final = events[-1]
    assert final["event"] == "repair.completed" and final["status"] == "passed"
    assert final["repair_rounds"] == 1
    assert final["build_id"] != build["build_id"]
    report = repair.verifier.get(p.project_id, final["report_id"])
    original = repair.verifier.get(p.project_id, prepared.report_id)
    assert report["cases"][0]["acceptance_hash"] == original["cases"][0]["acceptance_hash"]
    assert Compiler(studio).get(p.project_id, build["build_id"])["status"] == "rejected"
    assert repair.ledger.summary(p.project_id)["receipts"][0]["status"] == "completed"
    assert repair.ledger.summary(p.project_id, "runtime")["calls"] == 0
    request = repair.provider.requests[0]
    assert request["cases"][0]["input_text"] == "sample"
    assert request["cases"][0]["expected_text"] == "sample"
    studio.close()
    with StudioService.open_workspace(tmp_path / "studio") as reopened:
        from workflow_studio.llm.streaming import EventStore

        assert EventStore(reopened.repository).read(p.project_id, limit=1000)[-1] == final


def test_repair_requires_new_consent_and_rejects_changed_scope(tmp_path):
    studio, p, _, _, repair, prepared = setup(tmp_path)
    for consent in (None, "old-analysis-consent"):
        with pytest.raises(StudioError) as exc:
            list(repair.run(prepared, authorized_hash=consent))
        assert exc.value.code == "APPROVAL_REQUIRED"
    repair.provider.model = "other-model"
    with pytest.raises(StudioError) as exc:
        list(repair.run(prepared, authorized_hash=prepared.request_hash))
    assert exc.value.code == "REVISION_CONFLICT"
    assert repair.provider.calls == 0
    assert repair.ledger.summary(p.project_id)["calls"] == 0
    studio.close()


@pytest.mark.parametrize("change", ["goal", "evidence", "outputs"])
def test_model_cannot_expand_business_or_evidence_scope(tmp_path, change):
    def mutate(response, context, attempt):
        if change == "goal":
            response["ir"]["goal"]["success_criteria"] = ["always pass"]
        elif change == "evidence":
            response["ir"]["evidence"][0]["confidence_label"] = "human_confirmed"
            response["ir"]["evidence"][0]["verified_by"] = "made-up"
        else:
            response["ir"]["outputs"] = {"new": "artifact://copy/result"}

    studio, p, _, _, repair, prepared = setup(tmp_path, RepairProvider(mutate))
    before = studio.projects.get(p.project_id)
    with pytest.raises(StudioError):
        list(repair.run(prepared, authorized_hash=prepared.request_hash))
    assert repair.provider.calls <= 2
    assert studio.projects.get(p.project_id) == before
    studio.close()


def test_interruption_and_generator_close_keep_prior_revision_and_reserved_cost(tmp_path):
    studio, p, _, _, repair, prepared = setup(tmp_path, RepairProvider(interrupted=True))
    before = studio.projects.get(p.project_id)
    with pytest.raises(StudioError) as exc:
        list(repair.run(prepared, authorized_hash=prepared.request_hash))
    assert exc.value.code == "LLM_STREAM_INTERRUPTED"
    assert repair.provider.calls == 1
    assert studio.projects.get(p.project_id) == before
    assert repair.ledger.summary(p.project_id)["usage_unavailable"] == 1
    assert "private-provider" not in json.dumps(repair.events.read(p.project_id, limit=1000))
    stream = repair.run(prepared, authorized_hash=prepared.request_hash)
    assert next(stream)["event"] == "repair.started"
    stream.close()
    assert repair.ledger.summary(p.project_id)["receipts"][-1]["status"] == "interrupted"
    assert repair.events.read(p.project_id, limit=1000)[-1]["event"] == "repair.failed"
    studio.close()


def test_two_malformed_proposals_stop_without_replacing_revision(tmp_path):
    studio, p, _, _, repair, prepared = setup(tmp_path, RepairProvider(malformed=True))
    before = studio.projects.get(p.project_id)
    events = list(repair.run(prepared, authorized_hash=prepared.request_hash))
    assert repair.provider.calls == 2
    assert events[-1]["status"] == "failed"
    assert events[-1]["repair_rounds"] == 2
    assert studio.projects.get(p.project_id) == before
    assert all(r["status"] == "rejected" for r in repair.ledger.summary(p.project_id)["receipts"])
    studio.close()


def test_project_edit_while_streaming_prevents_proposal_commit(tmp_path):
    studio, p, _, _, repair, prepared = setup(tmp_path)
    stream = repair.run(prepared, authorized_hash=prepared.request_hash)
    assert next(stream)["event"] == "repair.started"
    current = studio.projects.get(p.project_id)
    studio.projects.update(p.project_id, current.version, name="concurrent edit")
    with pytest.raises(StudioError) as exc:
        list(stream)
    assert exc.value.code == "REVISION_CONFLICT"
    assert studio.projects.get(p.project_id).current_revision_id == current.current_revision_id
    studio.close()


def test_budget_and_cancel_prevent_provider_calls(tmp_path):
    studio, p, _, cases, repair, prepared = setup(tmp_path)
    with pytest.raises(StudioError):
        list(repair.run(prepared, authorized_hash=prepared.request_hash, cancelled=lambda: True))
    repair.budget = Budget(1, 1, 8000, 1_000_000, 1_000_000)
    prepared = repair.prepare(p.project_id, prepared.report_id, cases)
    with pytest.raises(StudioError) as exc:
        list(repair.run(prepared, authorized_hash=prepared.request_hash))
    assert exc.value.code == "BUDGET_EXCEEDED"
    assert repair.provider.calls == 0
    studio.close()


def test_original_binding_list_cannot_be_changed(tmp_path):
    studio, p, _, cases, repair, prepared = setup(tmp_path)
    with pytest.raises(StudioError, match="完全一致"):
        repair.prepare(p.project_id, prepared.report_id, cases + cases)
    assert repair.provider.calls == 0
    studio.close()


def test_missing_business_rule_is_preserved_as_a_question_without_retry(tmp_path):
    def ask(response, content, attempt):
        response["ir"] = None
        response["questions"] = ["門檻應包含剛好等於金額的項目嗎？"]

    studio, p, _, _, repair, prepared = setup(tmp_path, RepairProvider(ask))
    before = studio.projects.get(p.project_id)
    with pytest.raises(StudioError) as exc:
        list(repair.run(prepared, authorized_hash=prepared.request_hash))
    assert exc.value.code == "EVIDENCE_INSUFFICIENT"
    assert repair.provider.calls == 1
    events = repair.events.read(p.project_id, limit=1000)
    assert next(e for e in events if e["event"] == "repair.needs_clarification")["questions"] == [
        "門檻應包含剛好等於金額的項目嗎？"
    ]
    assert studio.projects.get(p.project_id) == before
    studio.close()


def test_ui_model_repair_completes_without_editing_code(tmp_path):
    from workflow_studio.conversation.session import StudioSession

    studio, p, build, _, repair, prepared = setup(tmp_path)
    runtime_python, provider, budget = (
        repair.verifier.runtime_python,
        repair.provider,
        repair.budget,
    )
    studio.close()
    model = StudioSession(tmp_path / "studio", runtime_python=runtime_python)
    model.select_project(p.project_id)
    model.provider, model.budget = provider, budget
    model.build_id = build["build_id"]
    prepared = model.prepare_repair(prepared.report_id)
    events = []
    report = model.repair_model(
        prepared, events.append, lambda: False, authorized_hash=prepared.request_hash
    )
    assert report["status"] == "passed"
    assert model.build_id == report["build_id"]
    assert model.approval_id is None
    assert model.revision().ir["revision"] == 2


@pytest.mark.parametrize("change", ["prompt", "endpoint", "budget"])
def test_runtime_model_repair_rebinds_scoped_credentials_and_budget(tmp_path, monkeypatch, change):
    import os

    from test_llm_runtime import llm_ir
    from workflow_studio.compiler.package import digest
    from workflow_studio.registry.policy import ToolPolicy
    from workflow_studio.verifier.authorization import RuntimeAuthorization, disclosure

    policy = ToolPolicy(
        "windows" if os.name == "nt" else "linux",
        frozenset({"workspace.write", "model.call", "secret.read"}),
        True,
        True,
        100_000,
    )
    expected = '{"summary":"correct"}\n'
    studio, p, build, cases, verifier, _ = verification_case(
        tmp_path, expected=expected, payload=llm_ir(), policy=policy
    )
    calls = []

    def fake_replay(package, root, source, python, *, runtime_env):
        calls.append(runtime_env)
        assert runtime_env["WORKFLOW_STUDIO_SECRET_PROVIDER_KEY"] == "fixture-secret"
        result = b'{"summary":"wrong"}\n' if len(calls) == 1 else expected.encode()
        folder = root / "output/artifacts/result"
        folder.mkdir(parents=True)
        (folder / "answer.json").write_bytes(result)
        (folder / "result.json").write_text(
            json.dumps({"result": {"path": "answer.json", "sha256": digest(result)}})
        )
        return {
            "named_outputs": {"result": {"primary": "artifacts/result/result.json"}},
            "run_id": "fixture-runtime",
            "lifecycle": ["run.finished"],
        }

    monkeypatch.setattr("workflow_studio.verifier.report.replay", fake_replay)
    original = disclosure(studio, p.project_id, build["build_id"], cases)
    report = verifier.verify(
        p.project_id,
        build["build_id"],
        cases,
        policy,
        authorization=RuntimeAuthorization(
            original["request_hash"], {"provider_key": "fixture-secret"}
        ),
    )
    assert report["status"] == "failed"

    def improve(response, context, attempt):
        response["ir"]["nodes"][0]["parameters"]["prompt"] = (
            "Return the corrected structured summary"
        )
        if change == "endpoint":
            response["ir"]["nodes"][0]["parameters"]["endpoint"] = (
                "https://unauthorized.invalid/chat"
            )
        elif change == "budget":
            response["ir"]["nodes"][0]["llm_policy"]["budget_micro_usd"] *= 2

    repair = ModelRepair(
        studio, RepairProvider(improve), Budget(5, 1_000_000, 8000, 1_000_000, 1_000_000), policy
    )
    prepared = repair.prepare(p.project_id, report["report_id"], cases)
    assert json.loads(prepared.scope_json)["maximum_runtime_budget_micro_usd"] == 200_000
    with pytest.raises(StudioError):
        list(repair.run(prepared, authorized_hash=prepared.request_hash))
    assert repair.provider.calls == 0
    if change != "prompt":
        with pytest.raises(StudioError) as exc:
            list(
                repair.run(
                    prepared,
                    authorized_hash=prepared.request_hash,
                    runtime_secrets={"provider_key": "fixture-secret"},
                )
            )
        assert exc.value.code == "APPROVAL_REQUIRED"
        assert len(calls) == 1
        studio.close()
        return
    events = list(
        repair.run(
            prepared,
            authorized_hash=prepared.request_hash,
            runtime_secrets={"provider_key": "fixture-secret"},
        )
    )
    assert events[-1]["status"] == "passed"
    assert len(calls) == 2
    assert (
        calls[0]["WORKFLOW_STUDIO_LLM_AUTHORIZATION"]
        != calls[1]["WORKFLOW_STUDIO_LLM_AUTHORIZATION"]
    )
    assert "fixture-secret" not in json.dumps(events)
    studio.close()


def test_two_model_repairs_fix_csv_algorithm_and_parameter_with_frozen_cases(tmp_path):
    from compiler_helpers import build_ir, runtime_python
    from generated_cases import csv_parameters
    from test_generated_handlers import program_ir
    from workflow_studio.conversation.session import StudioSession

    parameters = csv_parameters()
    parameters["program"] = parameters["program"].replace(">=", ">")
    parameters["arguments"]["minimum"] = 110
    payload = program_ir(parameters, "csv")
    payload["goal"]["description"] = "篩選未結帳且金額至少 100 的訂單"
    studio, p, revision, policy = build_ir(tmp_path, payload)
    studio.close()
    model = StudioSession(tmp_path / "studio", runtime_python=runtime_python())
    model.select_project(p.project_id)
    source, expected = tmp_path / "input.csv", tmp_path / "expected.csv"
    source.write_bytes(b"id,amount,status\nA,100,open\nB,150,closed\nC,105,open\nD,99,open\n")
    expected.write_bytes(b"id,amount\nA,100\nC,105\n")
    model.add_case(source, expected, "result")
    report = model.verify()
    assert report["status"] == "failed"
    assert report["cases"][0]["difference"]["text_diff"]

    def improve(response, context, attempt):
        parameters = response["ir"]["nodes"][0]["parameters"]
        if attempt == 1:
            parameters["program"] = parameters["program"].replace(" > ", " >= ")
        else:
            assert " >= " in parameters["program"]
            parameters["arguments"]["minimum"] = 100

    model.provider = RepairProvider(improve)
    model.budget = Budget(5, 1_000_000, 8000, 1_000_000, 1_000_000)
    prepared = model.prepare_repair(report["report_id"])
    events = []
    fixed = model.repair_model(
        prepared, events.append, lambda: False, authorized_hash=prepared.request_hash
    )
    assert fixed["status"] == "passed", fixed
    assert events[-1]["repair_rounds"] == 2
    assert model.revision().ir["revision"] == 3
    assert fixed["cases"][0]["acceptance_hash"] == report["cases"][0]["acceptance_hash"]
    assert all(
        request["cases"][0]["specification"]["comparison"] == "exact_bytes"
        for request in model.provider.requests
    )
