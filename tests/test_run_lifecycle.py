"""Run ownership and legal transitions are enforced by SQLite CAS."""
from concurrent.futures import ThreadPoolExecutor
import time
import shutil
import subprocess
import sys
import os
import threading
from pathlib import Path

import pytest

from flow_base.core.lifecycle import RunStore
from flow_base.runtime import FlowError, Runtime
from flow_base.workspace import Workspace
from flow_base.engine import run_workflow


def _store(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    runtime.initialize()
    return RunStore(runtime)


def test_idempotency_busy_and_spec_immutability(tmp_path):
    store = _store(tmp_path)
    run_id = store.create(job_id='job', revision_id='rev', spec_hash='spec-a', idempotency_key='same')
    assert store.create(job_id='job', revision_id='rev', spec_hash='spec-a', idempotency_key='same') == run_id
    with pytest.raises(FlowError) as error:
        store.create(job_id='job', revision_id='rev', spec_hash='spec-b', idempotency_key='same')
    assert error.value.code == 'IDEMPOTENCY_CONFLICT'
    with pytest.raises(FlowError) as error:
        store.create(job_id='another-job', revision_id='rev', spec_hash='spec-a', idempotency_key='same')
    assert error.value.code == 'IDEMPOTENCY_CONFLICT'
    with pytest.raises(FlowError) as error:
        store.create(job_id='other', revision_id='rev', spec_hash='spec-a')
    assert error.value.code == 'BUSY'
    assert store.get(run_id)['execution_spec_hash'] == 'spec-a'


def test_legal_transitions_require_delivery_for_success(tmp_path):
    store = _store(tmp_path)
    run_id = store.create(job_id='job', revision_id='rev', spec_hash='spec')
    assert store.transition(run_id, 'created', 'queued', version=0) == 1
    store.claim(run_id, token='token', pid=123, started_at=1.0)
    assert store.transition(run_id, 'preparing', 'running') == 3
    assert store.transition(run_id, 'running', 'publishing') == 4
    with pytest.raises(FlowError) as error:
        store.transition(run_id, 'publishing', 'succeeded')
    assert error.value.code == 'DELIVERY_REQUIRED'
    manifest = tmp_path / 'deliverables.json'
    manifest.write_text('{}', encoding='utf-8')
    store.record_delivery(run_id, manifest)
    manifest.write_text('changed', encoding='utf-8')
    with pytest.raises(FlowError) as error:
        store.transition(run_id, 'publishing', 'succeeded')
    assert error.value.code == 'DELIVERY_INVALID'
    manifest.write_text('{}', encoding='utf-8')
    store.transition(run_id, 'publishing', 'succeeded', result_status='review_required', delivery_status='published')
    row = store.get(run_id)
    assert row['execution_status'] == 'succeeded'
    assert row['result_status'] == 'review_required'
    assert row['ended_at']
    with pytest.raises(FlowError) as error:
        store.transition(run_id, 'succeeded', 'running')
    assert error.value.code == 'STATE_CONFLICT'


def test_hundred_claim_competitors_only_one_wins(tmp_path):
    store = _store(tmp_path)
    run_id = store.create(job_id='job', revision_id='rev', spec_hash='spec')
    store.transition(run_id, 'created', 'queued')

    def claim(index):
        try:
            store.claim(run_id, token=f'token-{index}', pid=100+index, started_at=index+1)
            return True
        except FlowError as error:
            assert error.code == 'STATE_CONFLICT'
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(claim, range(100)))
    assert results.count(True) == 1
    assert store.get(run_id)['state_version'] == 2


def test_hundred_cas_transitions_only_one_wins(tmp_path):
    store = _store(tmp_path)
    run_id = store.create(job_id='job', revision_id='rev', spec_hash='spec')

    def queue(_):
        try:
            store.transition(run_id, 'created', 'queued', version=0)
            return True
        except FlowError as error:
            assert error.code == 'STATE_CONFLICT'
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(queue, range(100)))
    assert results.count(True) == 1
    assert store.get(run_id)['state_version'] == 1


def test_two_processes_cannot_claim_same_run(tmp_path):
    store = _store(tmp_path)
    run_id = store.create(job_id='job', revision_id='rev', spec_hash='spec')
    store.transition(run_id, 'created', 'queued')
    code = (
        'import os,sys; from pathlib import Path; from flow_base.runtime import Runtime,FlowError; '
        'from flow_base.core.lifecycle import RunStore; '
        's=RunStore(Runtime(Path(sys.argv[1]))); '
        's.claim(sys.argv[2],token=str(os.getpid()),pid=os.getpid(),started_at=1.0)'
    )
    env = os.environ.copy()
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'routinekeel/flow_base/src')
    children = [subprocess.Popen([sys.executable, '-c', code, str(store.runtime.home), run_id],
                                 env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) for _ in range(2)]
    errors = [child.communicate(timeout=10)[1].decode('utf-8', 'replace') for child in children]
    codes = [child.returncode for child in children]
    assert sorted(codes) == [0, 1], errors


def test_cancel_request_is_not_cancelled_until_acknowledged(tmp_path):
    store = _store(tmp_path)
    run_id = store.create(job_id='job', revision_id='rev', spec_hash='spec')
    store.transition(run_id, 'created', 'queued')
    store.claim(run_id, token='token', pid=123, started_at=1.0)
    store.transition(run_id, 'preparing', 'running')
    store.request_cancel(run_id)
    assert store.get(run_id)['execution_status'] == 'cancelling'
    store.transition(run_id, 'cancelling', 'cancelled')
    assert store.get(run_id)['execution_status'] == 'cancelled'


def test_queued_cancel_has_no_process_to_stop(tmp_path):
    store = _store(tmp_path)
    run_id = store.create(job_id='job', revision_id='rev', spec_hash='spec')
    store.transition(run_id, 'created', 'queued')
    store.request_cancel(run_id)
    assert store.get(run_id)['execution_status'] == 'cancelled'


def test_unclaimed_run_eventually_releases_busy_workspace(tmp_path):
    store = _store(tmp_path)
    run_id = store.create(job_id='job', revision_id='rev', spec_hash='spec')
    store.transition(run_id, 'created', 'queued')
    assert store.reconcile(lambda *args: False, now=time.time()+21) == [run_id]
    assert store.get(run_id)['execution_status'] == 'interrupted'
    assert store.create(job_id='new-job', revision_id='rev', spec_hash='new-spec') != run_id


@pytest.fixture
def isolated_copy_module():
    names = [name for name in sys.modules if name == 'ai_flow02' or name.startswith('ai_flow02.')]
    previous = {name: sys.modules.pop(name) for name in names}
    try:
        yield
    finally:
        for name in list(sys.modules):
            if name == 'ai_flow02' or name.startswith('ai_flow02.'):
                sys.modules.pop(name)
        sys.modules.update(previous)


def test_copy_workflow_reaches_delivered_terminal_state(tmp_path, isolated_copy_module):
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / 'dist/workspace/檔案複製範例', tmp_path / 'copy_flow')
    workspace = Workspace(tmp_path)
    task = workspace.task('ai_flow02')
    workflow = task.configuration(workspace.runtime)
    source = tmp_path / 'input.txt'
    source.write_text('copy me', encoding='utf-8')
    result = run_workflow(workspace.runtime, task, workflow, source, tmp_path / 'result')
    execution = RunStore(workspace.runtime).get(result['run_id'])
    assert execution['execution_status'] == 'succeeded'
    assert execution['job_id'] == result['job_id']
    assert execution['delivery_status'] == 'published'
    with workspace.runtime.connect() as db:
        assert db.execute('SELECT count(*) FROM deliveries WHERE run_id=?', (result['run_id'],)).fetchone()[0] == 1
        assert db.execute('SELECT status FROM step_attempts WHERE run_id=?', (result['run_id'],)).fetchone()[0] == 'succeeded'
    again = run_workflow(workspace.runtime, task, workflow, source, tmp_path / 'result')
    assert again['run_id'] != result['run_id']
    with workspace.runtime.connect() as db:
        assert db.execute('SELECT status FROM step_attempts WHERE run_id=?', (again['run_id'],)).fetchone()[0] == 'cached'


def test_cancel_from_other_runtime_waits_for_handler_stop(tmp_path, monkeypatch, isolated_copy_module):
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / 'dist/workspace/檔案複製範例', tmp_path / 'copy_flow')
    workspace = Workspace(tmp_path)
    task = workspace.task('ai_flow02')
    workflow = task.configuration(workspace.runtime)
    source = tmp_path / 'input.txt'
    source.write_text('copy me', encoding='utf-8')
    started = threading.Event()
    adapter = task.load(workspace.runtime)
    from flow_base.core.run_context import current
    import flow_base.engine as engine
    real_supervisor = engine.Supervisor
    monkeypatch.setattr(engine, 'Supervisor', lambda runtime, run_id, context: real_supervisor(runtime, run_id, context, interval=0.02))

    def waiting_handler(ctx, step, deps, target):
        started.set()
        assert current(ctx.runtime).cancel_requested.wait(timeout=3)
        raise FlowError('RUN_CANCEL_REQUESTED', '已停止')

    monkeypatch.setitem(adapter.handlers, 'file.copy', waiting_handler)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_workflow, workspace.runtime, task, workflow, source, tmp_path / 'result')
        assert started.wait(timeout=3)
        run_id = workspace.runtime.history()[0]['run_id']
        other = RunStore(Runtime(workspace.runtime.home))
        other.request_cancel(run_id)
        assert other.get(run_id)['execution_status'] == 'cancelling'
        with pytest.raises(FlowError) as error:
            future.result(timeout=5)
        assert error.value.code == 'RUN_CANCEL_REQUESTED'
        assert other.get(run_id)['execution_status'] == 'cancelled'
        with other.runtime.connect() as db:
            assert db.execute('SELECT status FROM step_attempts WHERE run_id=?', (run_id,)).fetchone()[0] == 'cancelled'
