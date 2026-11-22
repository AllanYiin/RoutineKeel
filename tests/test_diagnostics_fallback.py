"""Secondary reporting faults must never replace the primary exception."""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from flow_base.cli import emit_error
from flow_base.contracts import Step, Workflow
from flow_base.diagnostics import cli_error, record_exception
from flow_base.engine import Adapter, run_workflow
from flow_base.observability.diagnostics import guard_secondary
from flow_base.presentation import Reporter
from flow_base.runtime import FlowError, Runtime
from flow_base.workspace import Workspace


def test_disk_and_db_failure_fall_back_to_safe_stderr(tmp_path, monkeypatch, capsys):
    runtime = Runtime(tmp_path / 'share_states')
    runtime.initialize()
    blocker = sqlite3.connect(runtime.home / 'state.sqlite3')
    blocker.execute('BEGIN EXCLUSIVE')
    blocker.execute("UPDATE schema_migrations SET applied_at=applied_at WHERE version=1")
    marker = 'private-marker-12345'
    def disk_failure(*args, **kwargs):
        raise OSError('disk full')
    monkeypatch.setattr('flow_base.diagnostics.atomic_json', disk_failure)
    monkeypatch.setattr('flow_base.diagnostics.atomic_bytes', disk_failure)
    try:
        original = FlowError('INPUT_MISSING', 'prompt: ' + marker)
        record = record_exception(original, tmp_path / 'readonly', runtime=runtime, run_id='run_test')
    finally:
        blocker.rollback()
        blocker.close()
    assert original.code == record['code'] == 'INPUT_MISSING'
    assert record['diagnostic_persistence'] == 'stderr_only'
    assert record['diagnostic_write_error'] == 'OSError'
    assert record['diagnostic_db_error'] == 'OperationalError'
    assert record['cause_status'] == 'confirmed'
    assert record['diagnostic_json'] is None and record['exception_log'] is None
    stderr = capsys.readouterr().err
    assert marker not in stderr
    assert record['error_id'] in stderr and 'INPUT_MISSING' in stderr


def test_json_serialization_failure_keeps_db_record(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path / 'share_states')
    runtime.initialize()
    def json_failure(*args, **kwargs):
        raise TypeError('cannot serialize')
    monkeypatch.setattr('flow_base.diagnostics.atomic_json', json_failure)
    original = ValueError('original')
    record = record_exception(original, tmp_path / 'logs', runtime=runtime, run_id='run_test')
    assert str(original) == 'original'
    assert record['diagnostic_persistence'] == 'run_log'
    assert record['diagnostic_write_error'] == 'TypeError'
    assert (tmp_path / 'logs/exception.log').is_file()
    with runtime.connect() as db:
        row = db.execute('SELECT code,cause_status,diagnostic_path FROM diagnostics WHERE error_id=?',
                         (record['error_id'],)).fetchone()
    assert tuple(row) == ('ValueError', 'unknown', None)


def test_description_failure_and_multilevel_causes_keep_identity(tmp_path):
    class BrokenMessage(Exception):
        def __str__(self):
            raise RuntimeError('secondary string failure')
    try:
        try:
            try:
                raise OSError('lowest')
            except OSError as low:
                raise ValueError('middle') from low
        except ValueError as middle:
            raise BrokenMessage() from middle
    except BrokenMessage as original:
        first = record_exception(original, tmp_path / 'first')
        second = record_exception(original, tmp_path / 'second')
    assert first['error_id'] == second['error_id']
    assert first['message'] == 'Exception details unavailable'
    assert [item['exception_type'] for item in first['causes']] == ['ValueError', 'OSError']


@pytest.mark.parametrize('secondary_site', ['reporter.fail', 'update_run', 'failure.json'])
def test_workflow_preserves_primary_when_reporting_fails(workspace, monkeypatch, secondary_site):
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    adapter = Adapter()
    original = ValueError('primary handler error')
    def fail_handler(*args):
        raise original
    adapter.handlers = {'fixture.fail': fail_handler}
    workflow = Workflow(id='fixture', steps=[Step(id='fail', handler='fixture.fail')])
    monkeypatch.setattr(task, 'load', lambda runtime: adapter)
    if secondary_site == 'reporter.fail':
        monkeypatch.setattr(Reporter, 'fail', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('reporter broke')))
    elif secondary_site == 'update_run':
        real_update = ws.runtime.update_run
        def fail_terminal_update(run_id, status, **kwargs):
            if kwargs.get('finished'):
                raise sqlite3.OperationalError('database locked')
            return real_update(run_id, status, **kwargs)
        monkeypatch.setattr(ws.runtime, 'update_run', fail_terminal_update)
    else:
        from flow_base import engine
        real = engine.atomic_json
        def fail_failure(path, data):
            if Path(path).name == 'failure.json':
                raise OSError('read-only failure report')
            return real(path, data)
        monkeypatch.setattr(engine, 'atomic_json', fail_failure)
    source = workspace / 'source.txt'
    source.write_text('input', encoding='utf-8')
    with pytest.raises(ValueError) as caught:
        run_workflow(ws.runtime, task, workflow, source, workspace / 'result')
    assert caught.value is original
    run_id = ws.runtime.history()[0]['run_id']
    logs = ws.runtime.logs / task.flow_id / run_id
    primary = json.loads((logs / 'error.json').read_text(encoding='utf-8'))
    assert primary['code'] == 'ValueError'
    secondary = list((logs / 'secondary').glob('*/error.json'))
    assert secondary
    assert any(json.loads(path.read_text(encoding='utf-8'))['parent_error_id'] == primary['error_id'] for path in secondary)


def test_secondary_diagnostic_failure_is_contained(tmp_path, monkeypatch):
    original = ValueError('original')
    primary = {'error_id': 'error_' + 'a'*32, 'diagnostic_json': str(tmp_path / 'error.json')}
    monkeypatch.setattr('flow_base.diagnostics.record_exception', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('secondary store failed')))
    assert guard_secondary(original, primary, 'reporter.fail', lambda: (_ for _ in ()).throw(RuntimeError('second'))) is None
    assert primary['secondary_errors'][0]['type'] == 'RuntimeError'


def test_cli_fallback_keeps_original_code_and_identity(tmp_path, monkeypatch, capsys):
    runtime = Runtime(tmp_path / 'share_states')
    runtime.initialize()
    def disk_failure(*args, **kwargs):
        raise OSError('read-only logs')
    monkeypatch.setattr('flow_base.diagnostics.atomic_json', disk_failure)
    monkeypatch.setattr('flow_base.diagnostics.atomic_bytes', disk_failure)
    original = FlowError('INPUT_MISSING', 'missing source')
    first = record_exception(original, tmp_path / 'run', runtime=runtime)
    result = cli_error(runtime, original)
    assert result['error']['code'] == 'INPUT_MISSING'
    assert result['error']['diagnostic']['error_id'] == first['error_id']
    assert result['error']['diagnostic']['diagnostic_persistence'] == 'db_only'
    assert 'INPUT_MISSING' not in capsys.readouterr().err  # DB retained the minimal record


def test_cli_emit_error_survives_diagnostic_constructor_failure(tmp_path, monkeypatch, capsys):
    runtime = Runtime(tmp_path / 'share_states')
    def fail(*args, **kwargs):
        raise RuntimeError('injected diagnostic failure')
    monkeypatch.setattr('flow_base.cli.cli_error', fail)
    result = emit_error(runtime, FlowError('INPUT_MISSING', 'private content'))
    assert result['error']['code'] == 'INPUT_MISSING'
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'INPUT_MISSING'


def test_action_preserves_primary_when_reporter_fails(workspace, monkeypatch):
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    task.manifest.actions.append('explode')
    original = RuntimeError('original action failure')
    def fail_action(*args):
        raise original
    task._adapter = SimpleNamespace(invoke_action=fail_action)
    monkeypatch.setattr(Reporter, 'fail', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('reporter disk error')))
    with pytest.raises(RuntimeError) as caught:
        task.invoke_action(ws.runtime, 'explode', {})
    assert caught.value is original
    run_id = ws.runtime.history()[0]['run_id']
    primary = json.loads((ws.runtime.logs / task.flow_id / run_id / 'error.json').read_text(encoding='utf-8'))
    assert primary['code'] == 'RuntimeError'
    secondary = list((ws.runtime.logs / task.flow_id / run_id / 'secondary').glob('*/error.json'))
    assert any(json.loads(path.read_text(encoding='utf-8'))['parent_error_id'] == primary['error_id'] for path in secondary)
