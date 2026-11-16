import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from flow_base.intent import Intent, local_intent, model_intent
from flow_base.runtime import FlowError, atomic_json, sha256
from flow_base.workspace import Workspace

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def workspace(tmp_path):
    for task in ('ai_flow01', 'ai_flow02'):
        shutil.copytree(ROOT / 'dist/workspace' / task, tmp_path / task)
    return tmp_path


def cli(workspace, *args, ok=True):
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'base/src'), PYTHONDONTWRITEBYTECODE='1')
    r = subprocess.run([sys.executable, '-m', 'flow_base', '--workspace', str(workspace), *map(str, args)],
                       env=env, capture_output=True, text=True, timeout=40)
    if ok:
        assert r.returncode == 0, r.stdout + r.stderr
    else:
        assert r.returncode != 0, r.stdout
    return json.loads(r.stdout)


def test_catalog_registers_flows_without_importing_tasks(workspace):
    data = cli(workspace, 'catalog')['result']
    assert len(data['tasks']) == 2
    assert (workspace / 'share_states/state.sqlite3').is_file()
    assert not (workspace / 'share_states/plugins').exists()
    assert all(row['flow_id'].startswith('flow_') for row in data['tasks'])


def test_base_alone_is_valid(tmp_path):
    assert cli(tmp_path, 'list')['result'] == []
    assert cli(tmp_path, 'run', '--task', 'ai_flow02', '--input', 'x', '--output', 'y', ok=False)['error']['code'] == 'TASK_UNKNOWN'


def test_two_task_packages_share_state_and_separate_logs(workspace):
    source = workspace / '測試 空格.txt'
    source.write_text('one runtime, multiple tasks\n')
    first = cli(workspace, 'run', '--task', 'ai_flow02', '--input', source, '--output', workspace/'copy')['result']
    again = cli(workspace, 'run', '--task', 'ai_flow02', '--input', source, '--output', workspace/'copy')['result']
    assert first['steps'][0]['status'] == 'executed'
    assert again['steps'][0]['status'] == 'cached'
    assert (workspace/'copy/artifacts/result.bin').read_bytes() == source.read_bytes()
    demo = cli(workspace, 'demo', '--task', 'ai_flow01', '--output', workspace/'demo')['result']
    assert demo['asr_executed'] is False
    assert (workspace/'demo/artifacts/subtitles.srt').is_file()
    assert (workspace/'share_states/state.sqlite3').is_file()
    assert first['flow_id'] == again['flow_id']
    assert first['run_id'] != again['run_id']
    assert first['job_id'] == again['job_id']
    assert (workspace/'share_logs'/first['flow_id']/first['run_id']/'report.json').is_file()
    assert not (workspace/'ai_flow02/.venv').exists()


def test_workspace_relocation_preserves_cache(workspace):
    (workspace/'data.txt').write_text('portable state')
    first = cli(workspace, 'run', '--task', 'ai_flow02', '--input', 'data.txt', '--output', 'out')['result']
    moved = workspace.parent / (workspace.name + '-moved')
    shutil.copytree(workspace, moved)
    second = cli(moved, 'run', '--task', 'ai_flow02', '--input', 'data.txt', '--output', 'out')['result']
    assert first['job_id'] == second['job_id']
    assert first['flow_id'] == second['flow_id'] and first['run_id'] != second['run_id']
    assert second['steps'][0]['status'] == 'cached'


def test_cache_corruption_recomputed(workspace):
    (workspace/'data.txt').write_text('correct')
    first = cli(workspace, 'run', '--task', 'ai_flow02', '--input', 'data.txt', '--output', 'out')['result']
    cache = workspace/'share_states/artifacts'/first['steps'][0]['cache_key']/'result.json'
    cache.write_text('corrupted')
    second = cli(workspace, 'run', '--task', 'ai_flow02', '--input', 'data.txt', '--output', 'out')['result']
    assert second['steps'][0]['status'] == 'executed'
    assert (workspace/'out/artifacts/result.bin').read_text() == 'correct'


def test_changed_output_not_overwritten(workspace):
    (workspace/'data.txt').write_text('input')
    (workspace/'out').mkdir()
    (workspace/'out/keep').write_text('keep')
    result = cli(workspace, 'run', '--task', 'ai_flow02', '--input', 'data.txt', '--output', 'out', ok=False)
    assert result['error']['code'] == 'OUTPUT_EXISTS'


def test_natural_language_copy_executes(workspace):
    (workspace/'講義.txt').write_text('檔案內容')
    result = cli(workspace, 'ask', '複製「講義.txt」存到「輸出」', '--execute')['result']
    assert result['status'] == 'complete'
    assert (workspace/'輸出/artifacts/result.bin').read_text() == '檔案內容'


def test_natural_language_missing_file_no_execution(workspace):
    result = cli(workspace, 'ask', '幫我產生字幕', '--execute')['result']
    assert result['status'] == 'needs_input' and result['executed'] is False


def test_ambiguous_workflows_ask(workspace):
    intent = local_intent(Workspace(workspace), '複製影片並產生字幕')
    assert intent.task is None and intent.questions


def test_negated_task_does_not_execute(workspace):
    result = cli(workspace, 'ask', '不要產生字幕', '--execute')['result']
    assert result['status'] == 'needs_input'


def test_currency_never_guessed(workspace):
    intent = local_intent(Workspace(workspace), '字幕預算上限兩元')
    assert intent.questions and not intent.overrides


def test_subtitle_language_rules(workspace):
    intent = local_intent(Workspace(workspace), '將「課程.mp4」做成英文字幕，使用 CPU')
    assert intent.task == 'ai_flow01' and intent.input == '課程.mp4'
    assert intent.overrides['options']['language'] == 'en'


def test_intent_cannot_supply_code(workspace):
    atomic_json(workspace/'intent.json', {'task':'ai_flow01', 'input':'x.mp4', 'overrides': {'profile_project':'/tmp/evil'}})
    result = cli(workspace, 'dispatch', '--intent', workspace/'intent.json', '--execute', ok=False)
    assert result['error']['code'] == 'INTENT_PARAMETER'


def test_intent_cannot_add_unknown_task(workspace):
    atomic_json(workspace/'intent.json', {'task': 'ai_flow999', 'input': 'x.txt'})
    assert cli(workspace, 'dispatch', '--intent', workspace/'intent.json', ok=False)['error']['code'] == 'TASK_UNKNOWN'


def test_unconfigured_polish_is_not_silently_skipped(workspace):
    result = cli(workspace, 'ask', '字幕請潤飾', '--input', 'x.mp4')
    assert result['result']['status'] == 'needs_input'


def test_task_wheel_tamper_is_detected(workspace):
    wheel = next((workspace/'ai_flow02').glob('*.whl'))
    wheel.write_bytes(wheel.read_bytes() + b'alteration')
    result = cli(workspace, 'plan', '--task', 'ai_flow02', ok=False)
    assert result['error']['code'] == 'TASK_INTEGRITY'


def test_archive_escape_is_rejected(workspace):
    folder = workspace/'ai_flow02'
    manifest = json.loads((folder/'flow.json').read_text())
    wheel = folder/manifest['package']
    with zipfile.ZipFile(wheel, 'w') as z:
        z.writestr('../escape', 'bad')
    manifest['sha256'] = sha256(wheel)
    atomic_json(folder/'flow.json', manifest)
    assert cli(workspace, 'plan', '--task', 'ai_flow02', ok=False)['error']['code'] == 'TASK_ARCHIVE'
    assert not (workspace/'escape').exists()


def test_plugin_cache_tamper_is_repaired(workspace):
    cli(workspace, 'plan', '--task', 'ai_flow02')
    file = next((workspace/'share_states/plugins').glob('*/ai_flow02/adapter.py'))
    file.write_text('raise RuntimeError("tamper")')
    assert cli(workspace, 'plan', '--task', 'ai_flow02')['result']['ready_to_prepare']
    assert 'tamper' not in file.read_text()


def test_router_cache_and_budget(workspace):
    config = dict(enabled=True, model='test', pricing_verified=True, pricing_note='test only',
                  input_usd_per_million=1, output_usd_per_million=2, budget_usd=0.25)
    atomic_json(workspace/'share_states/settings/router.json', config)
    calls = []
    def transport(*_):
        calls.append(1)
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'task':'ai_flow02','input':'x.txt'})}}],
                'usage': {'prompt_tokens':100, 'completion_tokens':20}}
    ws = Workspace(workspace)
    model_intent(ws, 'copy x.txt', transport=transport, selected=ws.task('ai_flow02'))
    _, usage = model_intent(ws, 'copy x.txt', transport=transport, selected=ws.task('ai_flow02'))
    assert len(calls) == 1 and usage['calls'] == 1
    config['budget_usd'] = 0.000001
    atomic_json(workspace/'share_states/settings/router.json', config)
    with pytest.raises(FlowError, match='budget'):
        model_intent(ws, 'different request', transport=transport, selected=ws.task('ai_flow02'))
    assert len(calls) == 1


def test_distributions_never_bundle_environment_or_models():
    import tomllib
    for name in ('flow_base', 'ai_flow01', 'ai_flow02'):
        project = 'base' if name == 'flow_base' else name
        version = tomllib.loads((ROOT/project/'pyproject.toml').read_text())['project']['version']
        archive_name = {'ai_flow01': 'flow_subtitles', 'ai_flow02': 'flow_copy'}.get(name, name)
        folder = {'ai_flow01': '課程影片產生字幕', 'ai_flow02': '檔案複製範例'}.get(name, name)
        with zipfile.ZipFile(ROOT/'dist'/f'{archive_name}-{version}.zip') as z:
            assert all(n.startswith(folder+'/') for n in z.namelist())
            assert not any('.venv' in n.split('/') or n.endswith('model.bin') for n in z.namelist())
            import io
            with zipfile.ZipFile(io.BytesIO(z.read(folder+'/source.zip'))) as source:
                assert not any('.venv' in n.split('/') for n in source.namelist())
    version = tomllib.loads((ROOT/'base/pyproject.toml').read_text())['project']['version']
    with zipfile.ZipFile(ROOT/'base/dist'/f'flow_base-{version}-py3-none-any.whl') as z:
        assert not any('subtitles' in n or 'worker.py' in n or 'profiles/' in n for n in z.namelist())


def test_failed_run_has_registered_ids(workspace):
    result = cli(workspace, 'run', '--task', 'ai_flow02', '--input', 'missing.txt', '--output', 'out', ok=False)
    details = result['error']['details']
    assert details['flow_id'].startswith('flow_') and details['run_id'].startswith('run_')
    rows = cli(workspace, 'history', '--task', 'ai_flow02')['result']
    assert rows[0]['run_id'] == details['run_id'] and rows[0]['status'] == 'failed'
    assert (workspace/'share_logs'/details['flow_id']/details['run_id']/'failure.json').is_file()


def test_update_preserves_flow_id(workspace):
    first = cli(workspace, 'list')['result']
    path = workspace/'ai_flow02/flow.json'
    manifest = json.loads(path.read_text())
    manifest['version'] = '0.2.9'
    atomic_json(path, manifest)
    after = cli(workspace, 'list')['result']
    assert first[1]['flow_id'] == after[1]['flow_id']
    assert after[1]['version'] == '0.2.9'


def test_concurrent_registration_only_creates_one_id(tmp_path):
    import concurrent.futures
    from flow_base.runtime import Runtime
    runtime = Runtime(tmp_path/'share_states')
    runtime.initialize()
    def register(_):
        return runtime.register_flow('ai_flow01', '0.2.0', 'x', 'test')
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(register, range(8)))
    assert len(set(ids)) == 1


def test_flow_id_is_an_executable_entry(workspace):
    (workspace/'input.txt').write_text('flow id dispatch')
    flow_id = next(t['flow_id'] for t in cli(workspace, 'list')['result'] if t['id'] == 'ai_flow02')
    result = cli(workspace, 'run', '--flow-id', flow_id, '--input', 'input.txt', '--output', 'out')['result']
    assert result['flow_id'] == flow_id and result['run_id'].startswith('run_')
