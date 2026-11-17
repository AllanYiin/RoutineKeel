"""Flight recorder durability, trace schema, filtering and bounded retention."""
import json
import os
import shutil
import subprocess
import sys
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from flow_base.observability.events import EventRecorder
from flow_base.observability.trace import span
from flow_base.core.run_context import RunContext, activate
from flow_base.runtime import run_process
from flow_base.runtime import FlowError, Runtime
from flow_base.workspace import Workspace
from flow_base.engine import run_demo, run_workflow
from test_workspace import workspace
from flow_base.contracts import AssetFile, Resource
from flow_base.resources import ensure_resource
from flow_base.diagnostics import record_exception
from flow_base.presentation import Reporter, append_console


def test_event_schema_redaction_and_cursor(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path / 'share_states')
    runtime.initialize()
    monkeypatch.setenv('TEST_API_KEY', 'marker-secret-12345')
    recorder = EventRecorder(runtime, 'run_test', tmp_path / 'logs')
    with span() as parent:
        first = recorder.record('step.started', attributes={'step_id': 'copy', 'prompt': 'private text',
                                                             'reason': 'marker-secret-12345'})
        with span():
            second = recorder.record('step.finished', attributes={'status': 'succeeded'})
    assert first['schema_version'] == '1.0'
    assert first['timestamp_utc'].endswith('Z')
    assert first['process_id'] == os.getpid()
    assert second['parent_span_id'] == parent.span_id
    text = (tmp_path / 'logs/flight.jsonl').read_text(encoding='utf-8')
    assert 'private text' not in text
    assert 'marker-secret-12345' not in text
    assert '[REDACTED]' in text
    page = recorder.read(limit=1)
    assert page['has_more'] is True and page['events'][0]['event_id'] == first['event_id']
    assert recorder.read(cursor=page['next_cursor'])['events'][0]['event_id'] == second['event_id']
    with runtime.connect() as db:
        assert db.execute('SELECT count(*) FROM event_index WHERE run_id=?', ('run_test',)).fetchone()[0] == 2


def test_fsynced_event_survives_index_failure(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path / 'share_states')
    runtime.initialize()
    recorder = EventRecorder(runtime, 'run_test', tmp_path / 'logs')
    original = recorder._index
    def crash(*args):
        raise RuntimeError('after fsync before index')
    monkeypatch.setattr(recorder, '_index', crash)
    with pytest.raises(RuntimeError, match='after fsync'):
        recorder.record('delivery.staged')
    assert recorder.read()['events'][0]['name'] == 'delivery.staged'
    monkeypatch.setattr(recorder, '_index', original)
    assert recorder.rebuild_index() == 1
    with runtime.connect() as db:
        assert db.execute('SELECT count(*) FROM event_index').fetchone()[0] == 1


def test_failed_preoperation_event_prevents_operation(tmp_path, monkeypatch):
    recorder = EventRecorder(None, 'run_test', tmp_path / 'logs')
    called = []
    def disk_failure(*args):
        raise OSError('injected disk failure')
    monkeypatch.setattr(recorder, '_write', disk_failure)
    with pytest.raises(FlowError) as error:
        recorder.record('delivery.staged')
        called.append('publish')
    assert called == []
    assert error.value.code == 'EVENT_PERSIST_FAILED'


def test_rotation_marks_truncation_and_bounds_files(tmp_path):
    recorder = EventRecorder(None, 'run_test', tmp_path / 'logs', max_bytes=1024, max_segments=2)
    first = recorder.record('step.started', attributes={'step_id': 'first'})
    for index in range(30):
        recorder.record('step.finished', attributes={'step_id': f'step_{index}', 'status': 'succeeded'})
    files = recorder._files()
    assert len(files) <= 3
    assert all(path.stat().st_size <= 1024 for path in files)
    assert any(event['name'] == 'recorder.truncated' for event in recorder.read()['events'])
    page = recorder.read(cursor=first['event_id'])
    assert page['truncated'] is True and page['events']


def test_legacy_events_and_console_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv('FLOW_BASE_OUTPUT', 'quiet')
    reporter = Reporter('fixture', 'flow_test', 'run_test', tmp_path / 'logs', 1)
    reporter.event_recorder.max_bytes = 1024
    reporter.event_recorder.max_segments = 2
    for index in range(30):
        reporter.emit('progress', status=str(index))
    legacy = list((tmp_path / 'logs').glob('events*.jsonl'))
    assert len(legacy) <= 3
    assert all(path.stat().st_size <= 1024 for path in legacy)
    assert 'log_truncated' in (tmp_path / 'logs/events.jsonl').read_text(encoding='utf-8')
    for index in range(30):
        append_console(tmp_path / 'logs/console.log', f'line-{index}-' + 'x'*150,
                       max_bytes=1024, max_segments=2)
    console = list((tmp_path / 'logs').glob('console*.log'))
    assert len(console) <= 3
    assert all(path.stat().st_size <= 1024 for path in console)
    assert 'truncated' in (tmp_path / 'logs/console.log').read_text(encoding='utf-8')


def test_concurrent_writers_keep_jsonl_lines_intact(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    runtime.initialize()
    logdir = tmp_path / 'logs'
    def write(index):
        EventRecorder(runtime, 'run_test', logdir).record('step.finished', attributes={'step_id': str(index)})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(100)))
    events = EventRecorder(runtime, 'run_test', logdir).read(limit=200)['events']
    assert len(events) == 100
    assert len({event['event_id'] for event in events}) == 100


def test_two_process_writers_share_single_lock(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    runtime.initialize()
    code = (
        'import sys; from pathlib import Path; from flow_base.runtime import Runtime; '
        'from flow_base.observability.events import EventRecorder; '
        'r=EventRecorder(Runtime(Path(sys.argv[1])),"run_test",Path(sys.argv[2])); '
        '[r.record("step.finished",attributes={"step_id":str(i)}) for i in range(25)]'
    )
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env['PYTHONPATH'] = str(root / 'routinekeel/flow_base/src')
    children = [subprocess.Popen([sys.executable, '-c', code, str(runtime.home), str(tmp_path / 'logs')],
                                 env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) for _ in range(2)]
    errors = [child.communicate(timeout=20)[1].decode('utf-8', 'replace') for child in children]
    assert [child.returncode for child in children] == [0, 0], errors
    assert len(EventRecorder(runtime, 'run_test', tmp_path / 'logs').read(limit=100)['events']) == 50


def test_workflow_records_before_after_and_delivery(tmp_path):
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / 'dist/workspace/檔案複製範例', tmp_path / 'copy_flow')
    source = tmp_path / 'input.txt'
    source.write_text('evidence', encoding='utf-8')
    env = os.environ.copy()
    env['PYTHONPATH'] = str(root / 'routinekeel/flow_base/src')
    result = subprocess.run([sys.executable, '-m', 'flow_base', '--workspace', str(tmp_path),
                             'run', '--task', 'ai_flow02', '--input', str(source),
                             '--output', str(tmp_path / 'output')],
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout[-500:]
    report = json.loads(result.stdout)['result']
    recorder = EventRecorder(Runtime(tmp_path / 'share_states'), report['run_id'],
                             tmp_path / 'share_logs' / report['flow_id'] / report['run_id'])
    events = recorder.read(limit=200)['events']
    assert Runtime(tmp_path / 'share_states').events(report['run_id'], limit=200)['events'] == events
    names = [event['name'] for event in events]
    expected = ['run.created', 'run.claim_attempt', 'run.claimed', 'run.preparing',
                'run.started', 'step.started', 'step.finished', 'delivery.staged',
                'delivery.verified', 'delivery.published', 'run.finished']
    assert [names.index(name) for name in expected] == sorted(names.index(name) for name in expected)
    step = next(event for event in events if event['name'] == 'step.started')
    finished = next(event for event in events if event['name'] == 'step.finished')
    assert step['step_attempt_id'] == finished['step_attempt_id']
    assert step['span_id'] == finished['span_id']
    assert step['parent_span_id'] is not None


def test_process_events_carry_run_attempt_and_span(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    runtime.initialize()
    recorder = EventRecorder(runtime, 'run_test', tmp_path / 'logs')
    context = RunContext(runtime, 'run_test', tmp_path / 'logs', object(),
                         event_sink=recorder, attempt_id='attempt_test')
    with activate(context):
        assert run_process([sys.executable, '-c', 'print("done")'], timeout=5).strip() == 'done'
    events = recorder.read()['events']
    assert [event['name'] for event in events] == ['process.spawn_attempt', 'process.spawned', 'process.exited']
    assert all(event['run_id'] == 'run_test' and event['step_attempt_id'] == 'attempt_test' for event in events)
    assert len({event['span_id'] for event in events}) == 1


def test_process_timeout_has_preceding_spawn_evidence(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    runtime.initialize()
    recorder = EventRecorder(runtime, 'run_test', tmp_path / 'logs')
    context = RunContext(runtime, 'run_test', tmp_path / 'logs', object(), event_sink=recorder)
    with activate(context):
        with pytest.raises(FlowError) as error:
            run_process([sys.executable, '-c', 'import time; time.sleep(2)'], timeout=0.1)
    assert error.value.code == 'PROCESS_TIMEOUT'
    assert [event['name'] for event in recorder.read()['events']] == [
        'process.spawn_attempt', 'process.spawned', 'process.timeout', 'process.exited']


def test_resource_check_records_invalid_before_offline_failure(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    runtime.initialize()
    recorder = EventRecorder(runtime, 'run_test', tmp_path / 'logs')
    resource = Resource(id='fixture', revision='1', license='test', files=[
        AssetFile(path='data.bin', url='https://example.invalid/data.bin', sha256='0'*64, size=1)])
    context = RunContext(runtime, 'run_test', tmp_path / 'logs', object(), event_sink=recorder)
    with activate(context):
        with pytest.raises(FlowError) as error:
            ensure_resource(runtime, resource, offline=True)
    assert error.value.code == 'OFFLINE_RESOURCE_MISSING'
    assert [event['name'] for event in recorder.read()['events']] == ['resource.verify_started', 'resource.invalid']


def test_failed_diagnostic_write_preserves_original_error(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path / 'share_states')
    runtime.initialize()
    recorder = EventRecorder(runtime, 'run_test', tmp_path / 'logs')
    context = RunContext(runtime, 'run_test', tmp_path / 'logs', object(), event_sink=recorder)
    original = ValueError('original problem')
    def fail_write(*args, **kwargs):
        raise OSError('injected full disk')
    monkeypatch.setattr('flow_base.diagnostics.atomic_json', fail_write)
    with activate(context):
        record = record_exception(original, tmp_path / 'diagnostic')
    assert str(original) == 'original problem'
    assert record['diagnostic_write_error'] == 'OSError'
    assert any(event['name'] == 'diagnostic.persist_failed' for event in recorder.read()['events'])


@pytest.mark.parametrize('failure_site', ['handler', 'publish'])
def test_failed_step_or_delivery_has_timeline(tmp_path, monkeypatch, failure_site):
    root = Path(__file__).resolve().parents[1]
    shutil.copytree(root / 'dist/workspace/檔案複製範例', tmp_path / 'copy_flow')
    previous = {name: sys.modules.pop(name) for name in list(sys.modules)
                if name == 'ai_flow02' or name.startswith('ai_flow02.')}
    try:
        workspace = Workspace(tmp_path)
        task = workspace.task('ai_flow02')
        workflow = task.configuration(workspace.runtime)
        adapter = task.load(workspace.runtime)
        def fail(*args):
            raise RuntimeError('injected handler failure')
        if failure_site == 'handler':
            monkeypatch.setitem(adapter.handlers, 'file.copy', fail)
        else:
            monkeypatch.setattr(adapter, 'publish', fail)
        source = tmp_path / 'input.txt'
        source.write_text('failure', encoding='utf-8')
        with pytest.raises(RuntimeError, match='injected handler'):
            run_workflow(workspace.runtime, task, workflow, source, tmp_path / 'output')
        run_id = workspace.runtime.history()[0]['run_id']
        logdir = workspace.runtime.logs / task.flow_id / run_id
        events = EventRecorder(workspace.runtime, run_id, logdir).read()['events']
        names = [event['name'] for event in events]
        if failure_site == 'handler':
            assert names.index('step.started') < names.index('step.failed') < names.index('run.failed')
            assert next(event for event in events if event['name'] == 'step.failed')['step_attempt_id'].startswith('attempt_')
        else:
            assert names.index('step.finished') < names.index('delivery.staged') < names.index('delivery.failed') < names.index('run.failed')
    finally:
        for name in list(sys.modules):
            if name == 'ai_flow02' or name.startswith('ai_flow02.'):
                sys.modules.pop(name)
        sys.modules.update(previous)


def test_demo_failure_has_attempt_and_before_after_events(workspace, monkeypatch):
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    def fail(*_args):
        raise RuntimeError('demo fixture failed')
    monkeypatch.setattr(task, 'load', lambda _runtime: SimpleNamespace(demo=fail))
    with pytest.raises(RuntimeError, match='demo fixture failed'):
        run_demo(ws.runtime, task, workspace / 'demo-failure')
    run_id = ws.runtime.history()[0]['run_id']
    with closing(ws.runtime.connect()) as db:
        attempt = db.execute('SELECT attempt_id,status FROM step_attempts WHERE run_id=?',
                             (run_id,)).fetchone()
    assert attempt['status'] == 'failed'
    events = ws.runtime.events(run_id, limit=100)['events']
    names = [event['name'] for event in events]
    assert names.index('step.started') < names.index('step.failed') < names.index('run.failed')
    assert all(event['step_attempt_id'] == attempt['attempt_id'] for event in events
               if event['name'] in {'step.started', 'step.failed'})
