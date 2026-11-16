import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from flow_base.codex_tokens import SessionMonitor, SessionReader, normalize, select_file
from flow_base.presentation import ACTIVE, Reporter
from flow_base.workspace import Task
from flow_base.runtime import Runtime


@pytest.fixture(autouse=True)
def isolated_host(monkeypatch, tmp_path):
    for key in ('CODEX_THREAD_ID', 'FLOW_BASE_CODEX_THREAD_ID', 'FLOW_BASE_CODEX_SESSION_FILE'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'no-host'))


def usage(ins=100, cached=60, out=20):
    return dict(input_tokens=ins, cached_input_tokens=cached, cache_write_input_tokens=0,
                output_tokens=out, reasoning_output_tokens=5, total_tokens=ins + out)


def new(values=None, identity='thread-a', rid='r1'):
    return {'type': 'token_usage_record', 'payload': {'thread_id': identity, 'session_id': 'process-id',
        'response_id': rid, 'thread_token_usage': values or usage(), 'usage': usage(10, 5, 5)}}


def old(values=None):
    return {'type': 'event_msg', 'payload': {'type': 'token_count', 'info': {
        'total_token_usage': values or usage(), 'last_token_usage': usage(10, 5, 5), 'model_context_window': 256000}}}


def notification(values=None, identity='thread-a'):
    mapping = {'input_tokens': 'inputTokens', 'cached_input_tokens': 'cachedInputTokens',
               'cache_write_input_tokens': 'cacheWriteInputTokens', 'output_tokens': 'outputTokens',
               'reasoning_output_tokens': 'reasoningOutputTokens', 'total_tokens': 'totalTokens'}
    total = {mapping[k]: v for k, v in (values or usage()).items()}
    return {'method': 'thread/tokenUsage/updated', 'params': {'threadId': identity, 'turnId': 'turn1',
             'tokenUsage': {'total': total, 'last': total, 'modelContextWindow': 256000}}}


def append(path, *events):
    with path.open('a', encoding='utf-8') as stream:
        for event in events:
            stream.write(json.dumps(event) + '\n')


def session(tmp_path, *events):
    path = tmp_path / 'rollout-thread-a.jsonl'
    append(path, {'type': 'session_meta', 'payload': {'id': 'thread-a'}},
           {'type': 'turn_context', 'payload': {'model': 'fixture-model'}}, *events)
    return path


@pytest.mark.parametrize('event', [new(), old(), notification()])
def test_three_receipt_formats(event, tmp_path):
    path = session(tmp_path, event)
    result = SessionReader(path, 'thread-a').read()
    assert result['status'] == 'available'
    assert result['usage']['total_tokens'] == 120
    assert result['usage']['uncached_input_tokens'] == 40
    assert result['model'] == 'fixture-model'


def test_new_format_wins_over_old_and_repeated_cumulative_is_not_added(tmp_path):
    path = session(tmp_path, new(), new(), old(usage(2, 1, 1)))
    result = SessionReader(path).read()
    assert result['usage']['input_tokens'] == 100
    assert result['source'] == 'token_usage_record.thread_token_usage'
    assert result['model_context_window'] == 256000


def test_partial_line_retry_and_invalid_json(tmp_path):
    path = session(tmp_path, new())
    reader = SessionReader(path)
    assert reader.read()['usage']['input_tokens'] == 100
    with path.open('a') as stream:
        stream.write('invalid\n' + json.dumps(new(usage(150, 80, 30))))
    assert reader.read()['usage']['input_tokens'] == 100
    with path.open('a') as stream:
        stream.write('\n')
    result = reader.read()
    assert result['usage']['input_tokens'] == 150 and result['invalid_lines'] == 1


def test_delta_freezes_and_does_not_add_cache_reasoning(tmp_path):
    path = session(tmp_path, new())
    monitor = SessionMonitor(file=path)
    append(path, new(usage(180, 90, 40), rid='r2'))
    result = monitor.summary(finish=True)
    assert result['run_delta']['input_tokens'] == 80
    assert result['run_delta']['cached_input_tokens'] == 30
    assert result['run_delta']['uncached_input_tokens'] == 50
    assert result['run_delta']['output_tokens'] == 20
    assert result['run_delta']['total_tokens'] == 100
    append(path, new(usage(300, 120, 50)))
    assert monitor.summary() == result


def test_no_new_usage_is_not_claim_of_free_host_turn(tmp_path):
    monitor = SessionMonitor(file=session(tmp_path, new()))
    result = monitor.summary()
    assert result['delta_status'] == 'no_new_usage'
    assert result['run_delta']['total_tokens'] == 0


def test_missing_baseline_does_not_treat_thread_total_as_run_delta(tmp_path):
    path = session(tmp_path)
    monitor = SessionMonitor(file=path)
    append(path, new())
    result = monitor.summary()
    assert result['status'] == 'available' and result['run_delta'] is None


@pytest.mark.parametrize('mode', ['counter_reset', 'file_replaced', 'schema_change'])
def test_discontinuous_counters_are_unknown(tmp_path, mode):
    path = session(tmp_path, old() if mode == 'schema_change' else new())
    monitor = SessionMonitor(file=path)
    if mode == 'counter_reset':
        append(path, new(usage(10, 5, 5)))
    elif mode == 'file_replaced':
        other = tmp_path / 'replacement'
        append(other, {'type': 'session_meta', 'payload': {'id': 'thread-a'}}, new(usage(150, 80, 30)))
        other.replace(path)
    else:
        append(path, new(usage(150, 80, 30)))
    result = monitor.summary()
    assert result['run_delta'] is None
    assert result['delta_status'] == 'source_changed_or_counter_reset'


def test_source_selection_never_guesses_current_window(tmp_path):
    root = tmp_path / 'sessions' / '2026'
    root.mkdir(parents=True)
    path = session(root, new())
    assert select_file(home=tmp_path)[0] is None
    assert select_file(session_id='thread-a', home=tmp_path)[0] == path
    assert select_file(latest=True, home=tmp_path)[1] == 'latest_unverified'
    archive = tmp_path / 'archived_sessions'
    archive.mkdir()
    (archive / path.name).write_bytes(path.read_bytes())
    assert select_file(session_id='thread-a', home=tmp_path)[0] is None


def test_notification_stream_filters_selected_thread_and_rejects_ambiguous(tmp_path):
    path = tmp_path / 'events.jsonl'
    append(path, notification(), notification(usage(900, 90, 90), identity='another-thread'))
    assert SessionReader(path, 'thread-a').read()['usage']['input_tokens'] == 100
    assert SessionReader(path).read()['status'] == 'unavailable'


def test_mismatched_session_metadata_and_missing_file(tmp_path):
    path = session(tmp_path, new())
    assert SessionReader(path, 'wrong-id').read()['status'] == 'unavailable'
    assert SessionMonitor(file=tmp_path / 'missing').summary()['status'] == 'unavailable'


def test_unknown_optional_fields_not_zero():
    result = normalize({'input_tokens': 100, 'output_tokens': 10})
    assert result['cached_input_tokens'] is result['reasoning_output_tokens'] is None
    assert result['uncached_input_tokens'] is None
    assert result['total_tokens'] == 110
    assert normalize({'input_tokens': True, 'output_tokens': 10}) is None
    assert normalize(usage(10, 20, 5))['cached_input_tokens'] is None


def test_only_metadata_is_retained_and_reported(tmp_path):
    path = session(tmp_path, {'type': 'response_item', 'payload': {'text': 'DO_NOT_COPY_TRANSCRIPT'}}, new())
    reader = SessionReader(path)
    result = reader.read()
    assert 'DO_NOT_COPY_TRANSCRIPT' not in json.dumps(result) + repr(reader.__dict__)


def test_cli_standalone_and_runtime_command(tmp_path):
    path = session(tmp_path, new())
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root / 'routinekeel/flow_base/src'))
    for module, extra in [('flow_base.codex_tokens', []), ('flow_base', ['tokens'])]:
        result = subprocess.run([sys.executable, '-m', module, *extra, '--file', str(path)],
                                env=env, capture_output=True, text=True, timeout=10)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data.get('result', data)['usage']['total_tokens'] == 120


@pytest.mark.parametrize('mode', ['human', 'json', 'quiet'])
def test_readable_banners_persist_and_preserve_multiline_links(tmp_path, monkeypatch, capsys, mode):
    monkeypatch.setenv('FLOW_BASE_OUTPUT', mode)
    reporter = Reporter('測試', 'f', 'r', tmp_path, 1)
    reporter.start()
    reporter.enter(SimpleNamespace(id='test', handler='unit.test'), 1, '第一步')
    reporter.complete('executed')
    reporter.end({'status': 'REVIEW_REQUIRED'}, {'label': '待人工確認\n確認頁：/中文/review.html\n下載：/file.zip'})
    console = (tmp_path / 'console.log').read_text()
    assert all(label in console for label in ('WORKFLOW START', 'STEP START', 'STEP DONE', 'WORKFLOW END'))
    assert '\n確認頁：/中文/review.html\n下載：/file.zip' in console
    assert '\x1b[' not in console
    assert 'REVIEW_REQUIRED' not in console  # Adapter's human label is used.
    assert '確認頁' in (tmp_path / 'live_status.md').read_text()
    captured = capsys.readouterr()
    assert captured.out == ''
    if mode == 'quiet':
        assert captured.err == ''
    elif mode == 'json':
        assert all(isinstance(json.loads(line), dict) for line in captured.err.splitlines())


@pytest.mark.parametrize('fail', [False, True])
def test_generic_action_gets_full_lifecycle_and_same_token_snapshot(tmp_path, monkeypatch, capsys, fail):
    path = session(tmp_path, new())
    monkeypatch.setenv('FLOW_BASE_CODEX_SESSION_FILE', str(path))
    runtime = Runtime(tmp_path / 'state')
    manifest = SimpleNamespace(actions=['review'], title='領域自訂流程')
    task = Task(tmp_path, manifest)
    task.flow_id = runtime.register_flow('test', '1.0.0', 'fixture', manifest.title)
    def invoke(runtime, name, parameters):
        assert ACTIVE.get() is not None
        append(path, new(usage(180, 90, 40)))
        if fail:
            raise ValueError('test failure')
        return {'decision': 'REVIEW_REQUIRED'}
    task._adapter = SimpleNamespace(invoke_action=invoke)
    if fail:
        with pytest.raises(ValueError):
            task.invoke_action(runtime, 'review', {})
    else:
        report = task.invoke_action(runtime, 'review', {})
        assert report['status'] == 'REVIEW_REQUIRED'
        assert report['token_usage']['host_agent_usage']['run_delta']['total_tokens'] == 100
    assert ACTIVE.get() is None
    events = next(runtime.logs.rglob('events.jsonl'))
    data = [json.loads(x) for x in events.read_text().splitlines()]
    assert data[0]['event'] == 'workflow_started'
    assert data[-1]['event'] == ('workflow_failed' if fail else 'workflow_finished')
    assert data[-1]['token_usage']['host_agent_usage']['run_delta']['total_tokens'] == 100
    assert '本次觀測增量' in capsys.readouterr().err
