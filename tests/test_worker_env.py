"""Worker subprocesses receive required runtime controls but no host secrets."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from flow_base.execution import process
from flow_base.execution.process import worker_environment
from flow_base.runtime import Runtime, run_process


def test_worker_environment_is_allowlisted(monkeypatch):
    marker = 'PRIVATE_WORKER_ENV_MARKER_123'
    for name in ('OPENAI_API_KEY', 'AWS_SECRET_ACCESS_KEY', 'HF_TOKEN',
                 'FLOW_BASE_API_KEY', 'CUSTOM_PASSWORD', 'PYTHONPATH'):
        monkeypatch.setenv(name, marker)
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES', '-1')
    env = worker_environment()
    assert marker not in json.dumps(env)
    assert env['CUDA_VISIBLE_DEVICES'] == '-1'
    assert env['HF_HUB_OFFLINE'] == '1'
    assert env['PYTHONUTF8'] == '1'


def test_windows_worker_environment_preserves_profile_architecture(monkeypatch):
    monkeypatch.setattr(process, "os", SimpleNamespace(name="nt"))
    source = {"PROCESSOR_ARCHITECTURE": "x86", "PROCESSOR_ARCHITEW6432": "AMD64"}
    env = worker_environment(source)
    assert {key: env[key] for key in source} == source


def test_runtime_worker_passes_allowlisted_env(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path / 'share_states')
    python = tmp_path / 'python.exe'
    python.touch()
    runtime.workers['fixture'] = tmp_path / 'worker.py'
    monkeypatch.setattr(runtime, 'environment', lambda profile: (tmp_path, python))
    monkeypatch.setenv('CUSTOM_PASSWORD', 'secret-marker-456')
    captured = {}

    def fake_process(argv, *, env, **kwargs):
        captured.update(env)
        request = json.loads(Path(argv[2]).read_text(encoding='utf-8'))
        Path(request['response_path']).write_text('{}', encoding='utf-8')

    monkeypatch.setattr('flow_base.runtime.run_process', fake_process)
    assert runtime.worker('fixture', {'action': 'verify'}, log=tmp_path / 'worker.log') == {}
    assert 'CUSTOM_PASSWORD' not in captured
    assert captured['TOKENIZERS_PARALLELISM'] == 'false'


def test_real_worker_process_cannot_read_host_secret(monkeypatch):
    marker = 'HOST_SECRET_MARKER_789'
    monkeypatch.setenv('CUSTOM_PASSWORD', marker)
    env = worker_environment()
    result = run_process([sys.executable, '-c',
                          'import os;print(os.environ.get("CUSTOM_PASSWORD","absent"))'], env=env)
    assert result.strip() == 'absent'
    assert marker not in json.dumps(env)
