import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError, model_validator

from flow_base.diagnostics import cli_error, record_exception
from flow_base.engine import run_workflow, Adapter
from flow_base.contracts import Workflow, Step
from flow_base.runtime import FlowError, read_json
from flow_base.workspace import Workspace
from test_workspace import workspace, cli


class ExampleRow(BaseModel):
    id: int
    text: str

    @model_validator(mode='after')
    def validate_timeline(self):
        raise ValueError('invalid word timeline')


def fail_validation():
    ExampleRow.model_validate({'id': 20, 'text': 'PRIVATE_TRANSCRIPT_SENTINEL'})


def test_validation_records_reason_id_stack_without_payload(tmp_path):
    try:
        fail_validation()
    except ValidationError as exc:
        result = record_exception(exc, tmp_path, run_id='run_fixture')
    assert result['validation_errors'][0]['record_id'] == 20
    assert 'invalid word timeline' in result['message']
    assert any(frame['function'] == 'fail_validation' for frame in result['stack'])
    for path in tmp_path.iterdir():
        assert 'PRIVATE_TRANSCRIPT_SENTINEL' not in path.read_text()
    assert 'record_id=20' in (tmp_path/'exception.log').read_text()


def test_run_and_repeated_cli_errors_keep_identity_and_link(tmp_path, monkeypatch):
    monkeypatch.setenv('FLOW_BASE_LAUNCH_ID', 'launch_'+'a'*32)
    runtime = SimpleNamespace(logs=tmp_path/'logs')
    try:
        fail_validation()
    except ValidationError as exc:
        run = record_exception(exc, tmp_path/'run', run_id='run_fixture')
        first = cli_error(runtime, exc)
        second = cli_error(runtime, exc)
    for error in [first['error'], second['error']]:
        diagnostic = error['diagnostic']
        assert diagnostic['error_id'] == run['error_id']
        assert diagnostic['run_diagnostic_json'] == run['diagnostic_json']
        assert diagnostic['launch_id'] == 'launch_'+'a'*32
        assert read_json(diagnostic['diagnostic_json']) == diagnostic


def test_causes_secrets_and_interrupts(tmp_path, monkeypatch):
    monkeypatch.setenv('EXAMPLE_API_KEY', 'secret-value-12345678')
    try:
        try:
            raise OSError('underlying failure')
        except OSError as original:
            raise FlowError('BROKEN', 'credential secret-value-12345678 Bearer abcdef123456',
                            api_key='another secret', transcript='private words', value=float('nan')) from original
    except FlowError as exc:
        record = record_exception(exc, tmp_path)
    assert record['causes'][0]['message'] == 'underlying failure'
    assert record['details']['value'] == 'nan'
    serialized = json.dumps(record)
    for secret in ['secret-value-12345678', 'abcdef123456', 'another secret', 'private words']:
        assert secret not in serialized
    try:
        raise KeyboardInterrupt()
    except KeyboardInterrupt as exc:
        record = record_exception(exc, tmp_path/'interrupted')
    assert record['stack'] and record['message'] == 'Execution interrupted'


def test_workflow_failure_details_reach_every_run_record(workspace, monkeypatch):
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    adapter = Adapter()
    adapter.handlers = {'fixture.validate': lambda *args: fail_validation()}
    workflow = Workflow(id='fixture', steps=[Step(id='validate', handler='fixture.validate')])
    monkeypatch.setattr(task, 'load', lambda runtime: adapter)
    source = workspace/'sample.txt'
    source.write_text('fixture')
    output = workspace/'result'
    with pytest.raises(ValidationError) as caught:
        run_workflow(ws.runtime, task, workflow, source, output)
    run_folder = next((workspace/'share_logs'/task.flow_id).glob('run_*'))
    error = read_json(run_folder/'error.json')
    assert 'record_id=20' in error['message']
    for path in [run_folder/'failure.json', run_folder/'report.json', output/'report.json']:
        saved = read_json(path)['error_details']
        assert saved['error_id'] == error['error_id']
        assert 'invalid word timeline' in saved['message']
    events = [json.loads(line) for line in (run_folder/'events.jsonl').read_text().splitlines()]
    assert events[-1]['error_details']['error_id'] == error['error_id']
    assert read_json(run_folder/'run.json')['status'] == 'failed'
    result = cli_error(ws.runtime, caught.value)
    assert result['error']['diagnostic']['run_diagnostic_json'] == str(run_folder/'error.json')
    for path in run_folder.iterdir():
        if path.is_file():
            assert 'PRIVATE_TRANSCRIPT_SENTINEL' not in path.read_text()


def test_preflight_cli_failure_has_persisted_diagnostic(tmp_path):
    result = cli(tmp_path, 'run', '--task', 'missing', '--input', 'x', ok=False)
    diagnostic = result['error']['diagnostic']
    assert Path(diagnostic['diagnostic_json']).is_file()
    assert diagnostic['stack']
    assert diagnostic['code'] == 'TASK_UNKNOWN'
