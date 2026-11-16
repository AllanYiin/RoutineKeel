"""Expired leases need verified process identity before reconciliation."""
import os
import subprocess
import sys
import threading
import time
import shutil
from pathlib import Path

import pytest

from flow_base.core.lifecycle import RunStore
from flow_base.execution.supervisor import Supervisor, identity_matches, process_started_at
from flow_base.runtime import FlowError, Runtime, run_process
from flow_base.workspace import Workspace
from flow_base.execution.supervisor import start_detached


def test_reconcile_marks_lost_run_interrupted(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    store = RunStore(runtime)
    run_id = store.create(job_id='job', revision_id='rev', spec_hash='spec')
    store.transition(run_id, 'created', 'queued')
    store.claim(run_id, token='token', pid=123, started_at=1.0, lease_seconds=-1)
    assert store.reconcile(lambda pid, birth, token: False) == [run_id]
    assert store.get(run_id)['execution_status'] == 'interrupted'
    assert store.reconcile(lambda pid, birth, token: False) == []


def test_live_verified_identity_keeps_expired_run(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    store = RunStore(runtime)
    run_id = store.create(job_id='job', revision_id='rev', spec_hash='spec')
    store.transition(run_id, 'created', 'queued')
    birth = process_started_at(os.getpid())
    assert birth is not None
    store.claim(run_id, token='token', pid=os.getpid(), started_at=birth, lease_seconds=-1)
    assert store.reconcile(identity_matches) == []
    assert store.get(run_id)['execution_status'] == 'preparing'
    assert not identity_matches(os.getpid(), birth - 10, 'token')
    assert not identity_matches(os.getpid(), birth, None)
    assert store.reconcile(lambda pid, started, token: identity_matches(pid, float(started)-10, token)) == [run_id]


def test_killed_supervisor_is_reconciled(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    store = RunStore(runtime)
    run_id = store.create(job_id='job', revision_id='rev', spec_hash='spec')
    store.transition(run_id, 'created', 'queued')
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
    try:
        birth = process_started_at(child.pid)
        assert birth is not None
        store.claim(run_id, token='token', pid=child.pid, started_at=birth, lease_seconds=-1)
        assert store.reconcile(identity_matches) == []
    finally:
        child.kill()
        child.wait(timeout=5)
    Runtime(runtime.home).initialize()  # reopening the project performs reconciliation
    assert store.get(run_id)['execution_status'] == 'interrupted'


def test_supervisor_observes_cancel_without_premature_terminal(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    store = RunStore(runtime)
    run_id = store.create(job_id='job', revision_id='rev', spec_hash='spec')
    store.transition(run_id, 'created', 'queued')
    from flow_base.core.run_context import RunContext
    from types import SimpleNamespace
    context = RunContext(runtime, run_id, tmp_path / 'logs', SimpleNamespace())
    with Supervisor(runtime, run_id, context, interval=0.02, lease_seconds=1):
        store.transition(run_id, 'preparing', 'running')
        store.request_cancel(run_id)
        assert store.get(run_id)['execution_status'] == 'cancelling'
        assert context.cancel_requested.wait(timeout=2)
    store.transition(run_id, 'cancelling', 'cancelled')


def test_process_cancellation_waits_for_exit(tmp_path):
    stop = threading.Event()
    timer = threading.Timer(0.1, stop.set)
    timer.start()
    try:
        with pytest.raises(FlowError) as error:
            run_process([sys.executable, '-c', 'import time; time.sleep(30)'],
                        timeout=10, log=tmp_path / 'worker.log', cancel_event=stop)
        assert error.value.code == 'PROCESS_CANCELLED'
    finally:
        timer.join(timeout=2)


def test_detached_supervisor_finishes_after_client_exits(tmp_path):
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / 'dist/workspace/檔案複製範例', tmp_path / 'copy_flow')
    source = tmp_path / 'input.txt'
    source.write_text('detached result', encoding='utf-8')
    code = (
        'import sys; from pathlib import Path; from flow_base.workspace import Workspace; '
        'from flow_base.execution.supervisor import start_detached; '
        'w=Workspace(Path(sys.argv[1])); t=w.task("ai_flow02"); '
        'v=t.configuration(w.runtime); '
        'a=start_detached(w,t,v,Path(sys.argv[2]),Path(sys.argv[3]),idempotency_key="same"); '
        'b=start_detached(w,t,v,Path(sys.argv[2]),Path(sys.argv[3]),idempotency_key="same"); '
        'assert a["run_id"]==b["run_id"]; print(a["run_id"],flush=True)'
    )
    parent = subprocess.run([sys.executable, '-c', code, str(tmp_path), str(source), str(tmp_path / 'result')],
                            capture_output=True, text=True, timeout=10)
    assert parent.returncode == 0, parent.stderr
    run_id = parent.stdout.strip().splitlines()[-1]
    runtime = Runtime(tmp_path / 'share_states')
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        runtime.initialize()
        row = RunStore(runtime).get(run_id)
        if row['execution_status'] in {'succeeded', 'failed', 'interrupted'}:
            break
        time.sleep(0.1)
    assert row['execution_status'] == 'succeeded', (row, (tmp_path / 'share_logs' / 'supervisor' / run_id / 'error.json').exists())
    assert (tmp_path / 'result/artifacts/result.bin').read_text(encoding='utf-8') == 'detached result'
    from flow_base.observability.events import EventRecorder
    with runtime.connect() as db:
        flow_id = db.execute('SELECT flow_id FROM runs WHERE run_id=?', (run_id,)).fetchone()[0]
    events = EventRecorder(runtime, run_id, tmp_path / 'share_logs' / flow_id / run_id).read()['events']
    created = next(event for event in events if event['name'] == 'run.created')
    claimed = next(event for event in events if event['name'] == 'run.claimed')
    assert created['process_id'] != claimed['process_id']
    assert created['run_id'] == claimed['run_id'] == run_id
    with runtime.connect() as db:
        assert db.execute('SELECT count(*) FROM run_executions').fetchone()[0] == 1
