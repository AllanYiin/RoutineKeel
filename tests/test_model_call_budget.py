import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from flow_base.graph.external import record_observation
from flow_base.graph.session import GraphSession
from flow_base.runtime import FlowError, read_json
from routinekeel_contracts.domain_assets import content_hash
from test_graph_builder import data_graph
from test_graph_review_external import approval
from test_graph_scheduler import setup


GUIDE = Path(__file__).resolve().parents[1] / "blueprints/binding-guides/course-video-subtitles"
RESOURCES = {"cost_micro_usd": 0, "model_call_batch": {"input": "segments", "size": 4}}


@pytest.fixture
def provider():
    pytest.importorskip("mcp.server.mcpserver")
    spec = importlib.util.spec_from_file_location("course_subtitles_host", GUIDE / "course_subtitles_host.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def boundary_cases():
    return json.loads((GUIDE / "model-budget-cases.json").read_bytes())


def fake_ollama(monkeypatch, provider, *, second=None):
    calls = []

    def send(request, *, timeout):
        payload = json.loads(request.data)
        segments = json.loads(payload["messages"][1]["content"])["segments"]
        calls.append(len(segments))
        if len(calls) == 2 and second == "timeout":
            raise TimeoutError("No model receipt")
        edits = [
            dict(id=segment["id"], original=segment["original"], revised=segment["original"],
                 changes=[], needs_review=False, deleted=False)
            for segment in segments
        ]
        return io.StringIO(json.dumps({
            "done": True, "done_reason": "length" if len(calls) == 2 and second else "stop",
            "message": {"content": json.dumps({"edits": edits})},
        }))

    monkeypatch.setattr(provider, "urlopen", send)
    return calls


@pytest.mark.parametrize("index,max_calls", [(0, 0), (1, 1), (2, 2), (3, 3)])
def test_course_counts_and_replays_each_claim_once(tmp_path, provider, monkeypatch, index, max_calls):
    case = boundary_cases()[index]
    segments = [provider.Segment(**item) for item in case["inputs"]["segments"]]
    budget = provider.ModelBudget(idempotency_key="a" * 64, scope_hash="b" * 64, max_calls=max_calls)
    calls = fake_ollama(monkeypatch, provider)
    result = provider.execute_copyedit(segments, budget, tmp_path)
    assert result["measurement"]["model_calls"] == len(calls) == max_calls
    assert result["outputs"] == case["expected"]
    assert provider.execute_copyedit(segments, budget, tmp_path) == result
    assert len(calls) == max_calls
    if max_calls > 1:
        legacy = budget.model_copy(update={"idempotency_key": "c" * 64, "max_calls": 1})
        with pytest.raises(ValueError, match="MODEL_BUDGET_CONFLICT"):
            provider.execute_copyedit(segments, legacy, tmp_path)
        assert len(calls) == max_calls


@pytest.mark.parametrize("failure,outcome", [("timeout", "unknown"), ("truncated", "failed")])
def test_second_batch_failure_preserves_partial_output_without_replay(tmp_path, monkeypatch, provider, failure,
                                                                      outcome):
    segments = [provider.Segment(**item) for item in boundary_cases()[-1]["inputs"]["segments"]]
    budget = provider.ModelBudget(idempotency_key="a" * 64, scope_hash="b" * 64, max_calls=3)
    calls = fake_ollama(monkeypatch, provider, second=failure)
    result = provider.execute_copyedit(segments, budget, tmp_path)
    assert result["outcome"] == outcome and result["outputs"] is None
    assert result["measurement"]["model_calls"] == 2
    assert result["completed_segments"] == 4
    stored = read_json(tmp_path / (budget.idempotency_key + ".json"))
    assert [item["id"] for item in stored["edits"]] == [item.id for item in segments[:4]]
    assert provider.execute_copyedit(segments, budget, tmp_path) == result
    assert calls == [4, 4]


def host_job(root, catalog_path, resources):
    graph = data_graph()
    array = {"type": "array", "items": {"type": "object"}}
    graph["inputs"] = {"segments": array}
    node = graph["nodes"][1]
    node.update(
        capability="test-sink", side_effect="external", permissions=[],
        inputs={"segments": {"source": "input://segments", "port": array}}, outputs={"edits": array},
    )
    graph.update(nodes=[node], outputs={"edits": "artifact://copy/edits"})
    graph["quality_contract"]["golden_cases"] = [{"inputs": {"segments": []}, "expected": {"edits": []}}]
    catalog = json.loads(catalog_path.read_bytes())
    port = {"type": "array", "required": True, "items": {"type": "object", "required": True}}
    catalog["tools"]["test-sink"].update(
        inputs={"segments": port}, outputs={"edits": port}, side_effect="external",
        permissions=[], resources=resources, network=False,
    )
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    return graph


def test_approved_scope_contains_computed_hard_limit_and_requires_measured_receipt(tmp_path, desktop_test_catalog):
    graph = host_job(tmp_path, desktop_test_catalog, RESOURCES)
    runtime, plan, bindings = setup(tmp_path, graph)
    data = boundary_cases()[-1]
    view = GraphSession.begin(runtime, plan, {"inputs": data["inputs"]}, tmp_path, bindings)
    session = GraphSession(runtime, view["session_id"])
    assert view["pending"]["scope"]["resources"]["model_calls"] == 3
    approved = session.interact("authorize", approval(view))
    claim = {"task_id": approved["pending"]["id"], "revision": approved["revision"], "request_id": "claim"}
    dispatch = session.interact("claim", claim)["dispatch"]
    assert "dispatch" not in session.interact("claim", claim)
    assert dispatch["arguments"]["model_budget"] == {
        "scope_hash": dispatch["scope_hash"], "idempotency_key": dispatch["idempotency_key"], "max_calls": 3,
    }
    waiting = session.interact("status", {})
    reply = {"task_id": claim["task_id"], "revision": waiting["revision"], "request_id": "reconcile"}
    assert session.interact("reconcile", reply)["code"] == "RECONCILE_REQUIRED"
    receipt = (tmp_path, dispatch["idempotency_key"], dispatch["scope_hash"], "observed", "succeeded", data["expected"])
    usage = {"model_calls": 3, "cost_micro_usd": 0}
    record_observation(*receipt, usage=usage)
    with pytest.raises(FlowError, match="用量觀測"):
        session.interact("reconcile", reply)
    measurement = dict(model_calls=3, elapsed_ms=3, batches=[
        dict(index=index, elapsed_ms=1, outcome="succeeded") for index in range(1, 4)
    ])
    record_observation(*receipt, usage=usage, measurement=measurement)
    assert session.interact("reconcile", reply)["outputs"] == data["expected"]


def test_fixed_one_call_budget_rejects_five_before_any_claim(tmp_path, desktop_test_catalog):
    resources = {**RESOURCES, "model_calls": 1}
    graph = host_job(tmp_path, desktop_test_catalog, resources)
    runtime, plan, bindings = setup(tmp_path, graph)
    view = GraphSession.begin(runtime, plan, {"inputs": boundary_cases()[2]["inputs"]}, tmp_path, bindings)
    assert view["nodes"]["copy"]["error"] == "MODEL_BUDGET_CONFLICT"
    assert not view.get("pending") and "dispatch" not in view


def test_installed_budget_conflict_opens_the_same_repair_without_dispatch(
    tmp_path, desktop_test_catalog, monkeypatch
):
    from flow_base.graph import rebind
    graph = host_job(tmp_path, desktop_test_catalog, {**RESOURCES, "model_calls": 1})
    runtime, plan, bindings = setup(tmp_path, graph)
    opened = []
    monkeypatch.setattr(rebind, "installed_graph", lambda *args, **kwargs: {
        "blueprint_sha256": plan["source_hash"], "source": b"fixture",
    })
    monkeypatch.setattr(rebind.installer, "recover_embedded_draft", lambda *args: plan["source_hash"])

    def start(service, operation, *, payload, origin):
        opened.append(origin)
        return {"work_id": "fixture-repair"}

    monkeypatch.setattr(rebind, "completion", start)
    request = {"inputs": boundary_cases()[2]["inputs"]}
    view = GraphSession.begin(runtime, plan, request, tmp_path, bindings, run_id="run_budget")
    assert view["code"] == "REBIND_REQUIRED" and view["rebind_work_id"] == "fixture-repair"
    assert "dispatch" not in view and not view.get("pending")
    again = GraphSession(runtime, view["session_id"]).interact("next", {})
    assert again["rebind_work_id"] == view["rebind_work_id"]
    assert len(opened) == 1
    assert opened[0]["budget_conflict"]["required_model_calls"] == 2
    assert opened[0]["budget_conflict"]["declared_model_calls"] == 1


@pytest.mark.parametrize("defect,status", [("missing", "NOT_RUN"), ("fixed", "FAIL"), ("output", "FAIL"), (None, "PASS")])
def test_qualification_derives_budget_and_output_verdicts_from_observations(monkeypatch, defect, status):
    from flow_base.graph import capabilities
    from flow_base.graph.qualification import _adapter_cases, _host_measure

    cases = boundary_cases()
    node = SimpleNamespace(inputs={"segments": None}, outputs={"edits": None})
    candidate = {
        "tool": "test-copyedit", "cost_micro_usd": 0, "environment_fingerprint": "environment",
        "model_version": "model-v1", "model_budget": dict(RESOURCES),
    }
    if defect == "fixed":
        candidate["model_budget"]["model_calls"] = 1
    case_hash = content_hash(cases)
    results = []
    for case, calls in zip(cases, (0, 1, 2, 3), strict=True):
        results.append({
            "id": case["id"], "input_hash": content_hash(case["inputs"]), "cost_micro_usd": 0,
            "output_hash": content_hash({} if defect == "output" and calls == 2 else case["expected"]),
            "measurement": None if defect == "missing" else {
                "model_calls": calls, "elapsed_ms": calls, "batches": [
                    dict(index=index, outcome="succeeded", elapsed_ms=1) for index in range(1, calls + 1)
                ],
            },
        })
    observed = dict(
        observation="executed", request_hash="request", case_hash=case_hash, model_version="model-v1",
        environment_fingerprint="environment", authorization_hash="a" * 64, results=results,
        conformance={key: "PASS" for key in ("inputs", "outputs", "permissions", "timeout", "side_effects", "budget")},
    )
    monkeypatch.setattr(capabilities, "host_catalog", lambda workspace: {"test-copyedit": {"portable_qualification": observed}})
    conformance, behavior = _host_measure(
        None, {"request_hash": "request", "case_hashes": {"edit": case_hash}},
        "edit", candidate, _adapter_cases(node, cases),
    )
    assert status == ("FAIL" if "FAIL" in (conformance["status"], behavior["status"]) else conformance["status"])
