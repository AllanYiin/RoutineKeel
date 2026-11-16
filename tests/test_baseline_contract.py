"""Stage 0 smoke checks for existing public CLI and error envelope."""
import json

from flow_base.cli import main


def _last_json(capsys):
    return json.loads(capsys.readouterr().out)


def test_empty_workspace_list_envelope(tmp_path, capsys):
    assert main(['--workspace', str(tmp_path), 'list']) == 0
    response = _last_json(capsys)
    assert response['ok'] is True
    assert response['result'] == []


def test_missing_task_error_envelope(tmp_path, capsys):
    assert main(['--workspace', str(tmp_path), 'plan', '--task', 'missing']) == 2
    response = _last_json(capsys)
    assert response['ok'] is False
    error = response['error']
    assert {'code', 'message', 'details', 'diagnostic'} <= error.keys()
    assert error['code'] == 'TASK_UNKNOWN'
    assert error['diagnostic']['error_id'].startswith('error_')
