"""Offline diagnosis keeps legacy JSON fields while offering safe human guidance."""
import ast
import json
import os
import re
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from flow_base.observability.errors import ALIASES, STANDARD, explain_error, explain_exception
from flow_base.observability.commands import doctor
from flow_base.runtime import FlowError, Runtime
from flow_base.runtime import require_space, run_process
from flow_base.contracts import Step
from flow_base.contracts import Workflow
from flow_base.engine import Adapter, run_workflow
from flow_base.core.lifecycle import RunStore
from flow_base.storage.artifacts import build_artifact_set
from flow_base.storage.delivery import write_version_manifest, rollback_delivery
from flow_base.observability.events import EventRecorder
from flow_base.workspace import Workspace
from flow_base.cli import diagnostic_command
from flow_base.presentation import Reporter
from test_workspace import workspace

ROOT = Path(__file__).resolve().parents[1]


def _cli(root, *arguments):
    env = os.environ.copy()
    env['PYTHONPATH'] = str(ROOT / 'routinekeel/flow_base/src')
    return subprocess.run([sys.executable, '-m', 'flow_base', '--workspace', str(root),
                           *map(str, arguments)], env=env, capture_output=True, text=True, timeout=30)


def test_every_base_flow_error_has_chinese_guidance_and_actions():
    source = ROOT / 'routinekeel/flow_base/src/flow_base'
    codes = {node.args[0].value for path in source.rglob('*.py')
             for node in ast.walk(ast.parse(path.read_text(encoding='utf-8')))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'FlowError'
             and node.args and isinstance(node.args[0], ast.Constant)
             and isinstance(node.args[0].value, str)}
    assert codes <= set(ALIASES) | set(STANDARD)
    for code in codes | set(STANDARD):
        item = explain_error(code)
        assert item['known'] == (code != 'UNKNOWN') and item['code'] == code
        assert item['category'] and item['severity'] and item['retry_policy'] and item['help_key']
        assert item['evidence_requirements']
        assert re.search(r'[\u4e00-\u9fff]', item['user_title'] + item['user_message'])
        assert item['suggested_actions'] and all(re.search(r'[\u4e00-\u9fff]', text)
                                                 for text in item['suggested_actions'])
        if code in codes:
            try:
                raise FlowError(code, 'synthetic catalog trigger')
            except FlowError as original:
                assert explain_exception(original)['canonical_code'] == item['canonical_code']


def test_nonzero_process_exit_does_not_claim_confirmed_oom():
    item = explain_exception(FlowError('PROCESS_FAILED', 'failed', exit_code=137),
                             {'cause_status': 'suspected', 'evidence': [{'kind': 'process_exit', 'exit_code': 137}]})
    assert item['canonical_code'] == 'WORKER_CRASHED'
    assert item['cause_status'] == 'suspected'
    assert 'OOM' not in item['user_message']


def test_representative_real_failures_map_to_standard_codes(tmp_path, monkeypatch):
    def mapped(call, canonical):
        with pytest.raises(Exception) as caught:
            call()
        assert explain_exception(caught.value)['canonical_code'] == canonical

    mapped(lambda: (tmp_path / 'missing.txt').open(), 'INPUT_NOT_FOUND')
    mapped(lambda: Step(id='bad id', handler='file.copy'), 'INVALID_CONFIGURATION')
    monkeypatch.setattr('flow_base.runtime.free_bytes', lambda path: 0)
    mapped(lambda: require_space(tmp_path / 'file', 1), 'DISK_SPACE_INSUFFICIENT')
    primary = tmp_path / 'result.json'
    primary.write_text('{}', encoding='utf-8')
    mapped(lambda: build_artifact_set(primary, ['missing-sidecar.txt']), 'RESOURCE_INTEGRITY_FAILED')
    runtime = Runtime(tmp_path / 'share_states')
    mapped(lambda: runtime.profile_source('missing'), 'DEPENDENCY_UNAVAILABLE')
    mapped(lambda: run_process([tmp_path / 'missing.exe'], log=tmp_path / 'start.log'), 'PROCESS_START_FAILED')
    mapped(lambda: run_process([sys.executable, '-c', 'import time;time.sleep(2)'], timeout=0.1,
                               grace_seconds=0.1), 'WORKER_TIMEOUT')
    mapped(lambda: run_process([sys.executable, '-c', 'import sys;sys.exit(1)']), 'WORKER_CRASHED')
    store = RunStore(runtime)
    run_id = store.create(job_id='job', revision_id='rev', spec_hash='spec')
    mapped(lambda: store.transition(run_id, 'created', 'running'), 'STATE_CONFLICT')
    stage = tmp_path / 'stage'
    stage.mkdir()
    mapped(lambda: write_version_manifest(stage, 'run_' + 'a'*32), 'DELIVERY_FAILED')
    mapped(lambda: rollback_delivery(runtime, tmp_path / 'job', 'delivery_missing'), 'PERMISSION_DENIED')
    recorder = EventRecorder(None, 'run_test', tmp_path / 'flight')
    monkeypatch.setattr(recorder, '_write', lambda *args: (_ for _ in ()).throw(OSError('disk failed')))
    mapped(lambda: recorder.record('delivery.staged'), 'DIAGNOSTIC_PERSIST_FAILED')


def test_doctor_is_read_only_and_json_has_no_noise(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    before = set(root.rglob('*'))
    result = doctor(Workspace(root))
    assert {item['name'] for item in result['checks']} == {'python', 'uv', 'workspace_permissions', 'sqlite', 'resources'}
    assert set(root.rglob('*')) == before
    process = _cli(root, 'doctor', '--format=json')
    assert process.returncode == 1
    assert process.stderr == ''
    assert json.loads(process.stdout)['result']['status'] == 'needs_attention'
    assert set(root.rglob('*')) == before


def test_doctor_checks_existing_sqlite_without_migrating_or_repairing(tmp_path):
    root = tmp_path / 'workspace'
    runtime = Runtime(root / 'share_states')
    runtime.initialize()
    before = {path.relative_to(root).as_posix(): path.stat().st_size
              for path in root.rglob('*') if path.is_file()}
    result = doctor(Workspace(root))
    sqlite = next(item for item in result['checks'] if item['name'] == 'sqlite')
    assert sqlite['status'] == 'ok'
    after = {path.relative_to(root).as_posix(): path.stat().st_size
             for path in root.rglob('*') if path.is_file()}
    assert after == before


def test_doctor_reports_corrupt_sqlite_without_repair(tmp_path):
    root = tmp_path / 'workspace'
    database = root / 'share_states/state.sqlite3'
    database.parent.mkdir(parents=True)
    database.write_bytes(b'not a sqlite database')
    result = doctor(Workspace(root))
    sqlite = next(item for item in result['checks'] if item['name'] == 'sqlite')
    assert sqlite['status'] == 'error'
    assert database.read_bytes() == b'not a sqlite database'
    assert not (database.parent / 'state.sqlite3.before-v001.sqlite3').exists()


def test_handler_contract_failure_maps_to_internal_catalog(workspace, monkeypatch):
    monkeypatch.setattr('flow_base.engine.require_space', lambda *args: None)
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    adapter = Adapter()
    def invalid_handler(ctx, step, dependencies, target):
        outside = workspace / 'outside.txt'
        outside.write_text('invalid', encoding='utf-8')
        return outside
    adapter.handlers = {'fixture.bad': invalid_handler}
    monkeypatch.setattr(task, 'load', lambda runtime: adapter)
    workflow = Workflow(id='invalid', steps=[Step(id='bad', handler='fixture.bad')])
    source = workspace / 'input.txt'
    source.write_text('input', encoding='utf-8')
    with pytest.raises(FlowError) as error:
        run_workflow(ws.runtime, task, workflow, source, workspace / 'result')
    assert error.value.code == 'HANDLER_CONTRACT'
    assert explain_exception(error.value)['canonical_code'] == 'UNEXPECTED_INTERNAL_ERROR'


def test_failed_run_can_be_diagnosed_explained_and_exported(workspace, monkeypatch):
    marker = 'PRIVATE_INPUT_MARKER_8374'
    failure = _cli(workspace, 'run', '--task', 'ai_flow02', '--input', marker + '.txt',
                   '--output', 'result')
    assert failure.returncode == 2
    error = json.loads(failure.stdout)['error']
    assert error['code'] == 'INPUT_MISSING'
    assert error['user_explanation']['canonical_code'] == 'INPUT_NOT_FOUND'
    run_id = error['details']['run_id']
    error_id = error['diagnostic']['error_id']
    diagnosed = _cli(workspace, 'diagnose', run_id, '--format=json')
    assert diagnosed.returncode == 1 and diagnosed.stderr == ''
    result = json.loads(diagnosed.stdout)['result']
    assert result['execution_status'] == 'failed'
    assert result['error_id'] == error_id
    assert result['error']['canonical_code'] == 'INPUT_NOT_FOUND'
    assert result['last_event']['name'] == 'run.failed'
    assert result['last_confirmed_event']['name'] in {'run.created', 'run.claimed'}
    listing = _cli(workspace, 'logs', run_id, '--format=json')
    assert listing.returncode == 0 and listing.stderr == ''
    listed = json.loads(listing.stdout)['result']
    assert {'error.json', 'flight.jsonl'} <= {item['name'] for item in listed['files']}
    assert 'console_tail' not in listed
    monkeypatch.setenv('EXAMPLE_API_KEY', 'secret-log-marker-98765')
    run_log = workspace / 'share_logs' / error['details']['flow_id'] / run_id
    with (run_log / 'console.log').open('a', encoding='utf-8') as output:
        output.write('Bearer token-value secret-log-marker-98765\n')
    tailed = _cli(workspace, 'logs', run_id, '--tail', '1', '--format=json')
    assert tailed.returncode == 0
    assert 'secret-log-marker-98765' not in tailed.stdout
    assert '[REDACTED]' in tailed.stdout
    explanation = _cli(workspace, 'explain-error', error_id, '--format=json')
    assert explanation.returncode == 0
    assert json.loads(explanation.stdout)['result']['explanation']['canonical_code'] == 'INPUT_NOT_FOUND'
    preview = _cli(workspace, 'export-diagnostics', run_id, '--format=json')
    assert preview.returncode == 0
    assert json.loads(preview.stdout)['result']['status'] == 'preview'
    target = workspace / 'safe-diagnostic.json'
    assert not target.exists()
    exported = _cli(workspace, 'export-diagnostics', run_id, '--format=json',
                    '--output', target, '--confirm')
    assert exported.returncode == 0
    assert json.loads(exported.stdout)['result']['status'] == 'exported'
    serialized = target.read_text(encoding='utf-8')
    assert marker not in serialized and 'stack' not in serialized
    assert json.loads(serialized)['diagnostic']['error_id'] == error_id
    repeated = _cli(workspace, 'export-diagnostics', run_id, '--format=json',
                    '--output', target, '--confirm')
    assert repeated.returncode == 2
    assert json.loads(repeated.stdout)['error']['code'] == 'OUTPUT_EXISTS'
    assert target.read_text(encoding='utf-8') == serialized


def test_human_explanation_and_invalid_query_exit_codes(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    human = _cli(root, 'explain-error', 'PROCESS_FAILED')
    assert human.returncode == 0 and '下一步' in human.stdout and '工作程序異常結束' in human.stdout
    invalid = _cli(root, 'diagnose', '../bad', '--format=json')
    assert invalid.returncode == 2
    assert json.loads(invalid.stdout)['error']['code'] == 'RUN_UNKNOWN'
    assert invalid.stderr == ''
    human_failure = _cli(root, 'diagnose', '../bad')
    assert human_failure.returncode == 2
    assert '下一步' in human_failure.stderr and 'Traceback' not in human_failure.stderr


def test_pre_run_launcher_error_can_be_found_by_error_id(tmp_path):
    root = tmp_path / 'workspace'
    root.mkdir()
    failed = _cli(root, 'run', '--task', 'missing', '--input', 'nothing.txt')
    assert failed.returncode == 2
    diagnostic = json.loads(failed.stdout)['error']['diagnostic']
    error_id = diagnostic['error_id']
    explained = _cli(root, 'explain-error', error_id, '--format=json')
    assert explained.returncode == 0
    result = json.loads(explained.stdout)['result']
    assert result['error_id'] == error_id
    assert result['explanation']['canonical_code'] == 'INPUT_NOT_FOUND'


def test_diagnostic_failure_falls_back_to_original_code(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'workspace'
    root.mkdir()
    def fail(*args, **kwargs):
        raise RuntimeError('secondary diagnostic broke')
    monkeypatch.setattr('flow_base.cli.cli_error', fail)
    result = diagnostic_command(Workspace(root), Namespace(command='diagnose', run_id='../bad', format='json'))
    assert result == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload['error']['code'] == 'RUN_UNKNOWN'
    assert payload['error']['diagnostic']['error_id'] == 'unavailable'


def test_internal_diagnostic_failure_returns_three_without_stack(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'workspace'
    root.mkdir()
    def fail(_workspace):
        raise RuntimeError('injected internal diagnostic failure')
    monkeypatch.setattr('flow_base.observability.commands.doctor', fail)
    result = diagnostic_command(Workspace(root), Namespace(command='doctor', format='json'))
    assert result == 3
    output = capsys.readouterr()
    assert json.loads(output.out)['error']['code'] == 'UNEXPECTED_ERROR'
    assert 'Traceback' not in output.out
    result = diagnostic_command(Workspace(root), Namespace(command='doctor', format='human'))
    assert result == 3
    output = capsys.readouterr()
    assert output.out == ''
    assert '錯誤編號' in output.err and 'Traceback' not in output.err


def test_human_error_card_uses_catalog_without_stack(tmp_path, monkeypatch):
    monkeypatch.setenv('FLOW_BASE_OUTPUT', 'quiet')
    reporter = Reporter('範例', 'flow_test', 'run_test', tmp_path / 'logs', 1)
    reporter.fail('PROCESS_FAILED', 'PRIVATE_PROMPT_CONTENT',
                  diagnostic={'error_id': 'error_' + 'a'*32, 'cause_status': 'suspected'})
    visible = (tmp_path / 'logs/console.log').read_text(encoding='utf-8')
    assert '工作程序異常結束' in visible and '下一步：' in visible
    assert '確定程度：可能' in visible and 'error_' + 'a'*32 in visible
    assert 'PRIVATE_PROMPT_CONTENT' not in visible and 'Traceback' not in visible
    assert 'PRIVATE_PROMPT_CONTENT' not in (tmp_path / 'logs/events.jsonl').read_text(encoding='utf-8')
