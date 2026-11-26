from concurrent.futures import ThreadPoolExecutor
import importlib
from threading import Barrier, Event

import pytest
from flow_base.blueprints.binding_operations import BindingOperation, BindingOperations
from flow_base.core.service import WorkflowService
from flow_base.runtime import FlowError, Runtime
from test_blueprint_completion import gap_archive


def test_binding_batch_overlaps_ready_operations_and_unblocks_dependent_work(tmp_path):
    store = BindingOperations(Runtime(tmp_path / "share_states"), "fixture-work")
    first = BindingOperation("qualify", "first")
    second = BindingOperation("qualify", "second")
    dependent = BindingOperation("qualify", "dependent", (first.id, second.id))
    barrier = Barrier(2, timeout=5)

    def measure():
        barrier.wait()
        return {"verdict": "PASS"}

    def dependent_check():
        assert all(row["status"] == "succeeded" for row in store.results() if row["operation_id"] != dependent.id)
        return {"verdict": "PASS"}

    jobs = [
        (dependent, dependent_check, lambda _result: True),
        (first, measure, lambda _result: True),
        (second, measure, lambda _result: True),
    ]
    expected = {job[0].id: {"verdict": "PASS"} for job in jobs}
    assert store.run(jobs, slots=2) == expected
    assert store.run(jobs, slots=2) == expected
    assert all(row["generation"] == 1 for row in store.results())


def test_lease_is_atomic_and_expired_external_claim_cannot_be_resent(tmp_path):
    runtime = Runtime(tmp_path / "share_states")
    stores = [BindingOperations(runtime, "fixture-work") for _ in range(2)]
    local = BindingOperation("prepare", "local")
    external = BindingOperation("dispatch", "external", resource_key="service", replay_safe=False)
    stores[0].register([local, external])
    barrier = Barrier(2, timeout=5)

    def claim(store):
        barrier.wait()
        return store.claim(local.id)

    with ThreadPoolExecutor(2) as pool:
        original, = [lease for lease in pool.map(claim, stores) if lease is not None]
    claimed = stores[0].claim(external.id, lease_seconds=-1)
    assert claimed
    with runtime.connect() as db:
        db.execute("UPDATE binding_operations SET lease_expires = 0 WHERE operation_id = ?", (local.id,))
    resumed = stores[1].claim(local.id)
    assert resumed.generation == original.generation + 1
    assert stores[1].claim(external.id) is None
    assert {row["kind"]: row["status"] for row in stores[0].results()}["dispatch"] == "reconcile_required"
    with pytest.raises(FlowError, match="認領已過期"):
        stores[0].finish(original, succeeded=True, result={})
    stores[1].finish(resumed, succeeded=True, result={"recovered": True})


def test_completion_can_be_cancelled_while_a_binding_worker_is_outside_the_state_lock(tmp_path, monkeypatch):
    module = importlib.import_module("flow_base.blueprints.completion")
    archive = gap_archive(tmp_path / "source.zip")
    started, release = Event(), Event()
    service = WorkflowService(tmp_path)

    def prepare(service, state, checkpoint, *, retry=False):
        staged = checkpoint({**state, "status": "preparing"})
        started.set()
        assert release.wait(timeout=10)
        return staged

    monkeypatch.setattr(module, "_prepare", prepare)
    with ThreadPoolExecutor(2) as pool:
        original = pool.submit(
            service.blueprint_completion, "start",
            payload={"archive": str(archive), "request_id": "cancel-during-probe"},
        )
        try:
            assert started.wait(timeout=10)
            from flow_base.runtime import digest

            work_id = "completion_" + digest("cancel-during-probe")[:32]
            current = service.blueprint_completion("status", work_id)
            cancelled = service.blueprint_completion(
                "cancel", work_id, payload={"expected_revision": current["revision"], "request_id": "cancel"},
            )
            assert cancelled["status"] == "cancelled"
        finally:
            release.set()
        with pytest.raises(FlowError, match="補強工作已更新"):
            original.result(timeout=10)
    assert service.blueprint_completion("status", work_id)["status"] == "cancelled"


def test_interrupted_binding_drive_reuses_start_request_and_revision(tmp_path, monkeypatch):
    module = importlib.import_module("flow_base.blueprints.completion")
    service = WorkflowService(tmp_path)
    archive = gap_archive(tmp_path / "source.zip")
    snapshots = []
    original_prepare = module._prepare

    def prepare(service, state, checkpoint, *, retry=False):
        if not snapshots:
            snapshots.append(checkpoint({**state, "status": "preparing"}))
            raise RuntimeError("fixture worker interrupted after saving the operation")
        return original_prepare(service, state, checkpoint, retry=retry)

    monkeypatch.setattr(module, "_prepare", prepare)
    request = {"archive": str(archive), "request_id": "interrupted-binding-drive"}
    with pytest.raises(RuntimeError, match="fixture worker interrupted"):
        service.blueprint_completion("start", payload=request)
    resumed = service.blueprint_completion("start", payload=request)
    assert resumed["work_id"] == snapshots[0]["work_id"]
    assert resumed["draft_id"] == snapshots[0]["draft_id"]
    assert resumed["revision"] > snapshots[0]["revision"]
    assert service.blueprint_completion("start", payload=request) == resumed


def test_preparation_overlaps_qualification_on_another_ready_branch(monkeypatch):
    module = importlib.import_module("flow_base.blueprints.completion")
    barrier = Barrier(2, timeout=5)
    updates = []
    state = {
        "status": "needs_input", "draft_id": "fixture", "blueprint_schema_version": 2,
        "gaps": [{"kind": "capability", "candidate_rejections": [{"recipe_id": "missing"}]}],
    }

    def prepare(service, state, record, *, retry=False):
        barrier.wait()
        return {**state, "preparations": {"missing": {"status": "prepared"}}}

    def qualify(service, state, record, *, retry=False):
        if "preparations" not in state:
            barrier.wait()
            return {**state, "qualification": {"independent": "PASS"}}
        assert state["qualification"]["independent"] == "PASS"
        return {**state, "qualification": {**state["qualification"], "missing": "PASS"}}

    monkeypatch.setattr(module, "_prepare", prepare)
    monkeypatch.setattr(module, "_qualify", qualify)
    monkeypatch.setattr(module, "_preflight", lambda _service, state: state)
    result = module._advance_bindings(None, state, updates.append, retry=False)
    assert result["qualification"] == {"independent": "PASS", "missing": "PASS"}
    assert updates[0]["status"] == "preparing"
