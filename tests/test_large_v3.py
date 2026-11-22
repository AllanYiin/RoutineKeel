import json
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest
from ai_flow01 import adapter, worker
from ai_flow01.contracts import subtitle_workflow
from ai_flow01.resources import catalogue
from flow_base.runtime import FlowError, Runtime, run_process

ROOT = Path(__file__).resolve().parents[1]


def test_large_v3_is_default_and_has_complete_pinned_resources():
    flow = subtitle_workflow()
    assert flow.model == 'whisper-large-v3'
    assert flow.options.allow_cpu_fallback is False
    resource = catalogue()[flow.model]
    assert len(resource.revision) == 40
    assert {f.path for f in resource.files} == {'model.bin', 'config.json', 'preprocessor_config.json', 'tokenizer.json', 'vocabulary.json'}
    assert all('/resolve/' + resource.revision + '/' in f.url for f in resource.files)
    assert sum(f.size for f in resource.files) > 3_000_000_000
    assert len({f.sha256 for f in resource.files}) == len(resource.files)
    manifest = json.loads((ROOT/'workflows/FLOW_課程影片產生字幕/flow.template.json').read_text())
    assert manifest['defaults']['model'] == flow.model
    assert any(r.get('set', {}).get('model') == flow.model for r in manifest['rules'])


@pytest.mark.parametrize('profile', ['asr-cpu', 'asr-cuda'])
def test_legacy_ctranslate2_compatibility_is_locked_for_every_platform(profile):
    root = ROOT/'workflows/FLOW_課程影片產生字幕/src/ai_flow01/profiles'/profile
    project = tomllib.loads((root/'pyproject.toml').read_text())
    lock = tomllib.loads((root/'uv.lock').read_text())
    assert 'setuptools==80.9.0' in project['project']['dependencies']
    assert next(p for p in lock['package'] if p['name'] == 'setuptools')['version'] == '80.9.0'


def test_worker_failure_shows_traceback_from_log(tmp_path):
    log = tmp_path/'worker.log'
    with pytest.raises(FlowError) as caught:
        run_process([sys.executable, '-c', "raise ModuleNotFoundError(\"No module named 'pkg_resources'\")"], log=log)
    assert caught.value.code == 'PROCESS_FAILED'
    assert 'pkg_resources' in caught.value.details['output']
    assert 'Traceback' in caught.value.details['output']
    assert caught.value.details['log'] == str(log)


def test_failure_tail_is_bounded_and_contains_final_error(tmp_path):
    with pytest.raises(FlowError) as caught:
        run_process([sys.executable, '-c', "print('x'*30000, flush=True); raise RuntimeError('最後錯誤')"], log=tmp_path/'worker.log')
    assert len(caught.value.details['output']) <= 8000
    assert '最後錯誤' in caught.value.details['output']


def test_dependency_failure_happens_before_model_download(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path/'share_states')
    monkeypatch.setattr(adapter, 'plan', lambda *_: {'blocked': []})
    monkeypatch.setattr(runtime, 'prepare_profile', lambda *_args, **_kwargs: None)
    calls = []
    def check(profile, request, **kwargs):
        calls.append(request['action'])
        raise FlowError('PROCESS_FAILED', 'missing dependency', output='pkg_resources')
    monkeypatch.setattr(runtime, 'worker', check)
    monkeypatch.setattr(adapter, 'ensure_resource', lambda *_args, **_kwargs: pytest.fail('must not download model'))
    with pytest.raises(FlowError):
        adapter.prepare(runtime, subtitle_workflow())
    assert calls == ['dependencies']


def test_cuda_unavailable_does_not_silently_switch_device(monkeypatch):
    monkeypatch.setattr(worker, 'configure_cuda_libraries', lambda: None)
    monkeypatch.setitem(sys.modules, 'ctranslate2', SimpleNamespace(get_cuda_device_count=lambda: 0))
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=lambda *a, **kw: pytest.fail('must not load a different device')))
    with pytest.raises(RuntimeError, match='CUDA_UNAVAILABLE'):
        worker.load_model({'model_path': 'same-large-v3', 'device': 'auto', 'allow_cpu_fallback': False})


def test_memory_block_preserves_model(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path/'share_states')
    original = adapter.inspect_environment
    def low_memory(*args):
        env = original(*args)
        env['memory']['available'] = 2 * 1024**3
        return env
    monkeypatch.setattr(adapter, 'inspect_environment', low_memory)
    flow = subtitle_workflow()
    plan = adapter.plan(runtime, flow)
    assert any(row['code'] == 'MEMORY_LOW' for row in plan['blocked'])
    assert plan['recognition']['model'] == 'whisper-large-v3' and flow.model == 'whisper-large-v3'


def test_settings_example_is_valid_and_does_not_enable_paid_calls():
    config = json.loads((ROOT/'workflows/FLOW_課程影片產生字幕/settings.example.json').read_text())
    flow = adapter.Adapter().configuration(config)
    assert flow.model == 'whisper-large-v3' and not flow.options.llm.enabled


def test_gpu_failure_reuses_identical_model_path(monkeypatch):
    monkeypatch.setattr(worker, 'configure_cuda_libraries', lambda: None)
    monkeypatch.setitem(sys.modules, 'ctranslate2', SimpleNamespace(get_cuda_device_count=lambda: 1))
    paths = []
    def model(path, **kwargs):
        paths.append(path)
        if kwargs['device'] == 'cuda':
            raise RuntimeError('GPU unavailable')
        return object()
    monkeypatch.setitem(sys.modules, 'faster_whisper', SimpleNamespace(WhisperModel=model))
    worker.load_model({'device': 'cuda', 'model_path': 'pinned-large-v3', 'allow_cpu_fallback': True})
    assert paths == ['pinned-large-v3', 'pinned-large-v3']
