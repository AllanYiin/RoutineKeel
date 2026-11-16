import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from flow_base.runtime import atomic_json
from flow_base.workspace import Workspace
from test_workspace import workspace, cli, ROOT

spec = importlib.util.spec_from_file_location('entry_profile', ROOT/'flow_base/scripts/flow_base_profile.py')
profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(profile)


def modify(folder, **changes):
    path = folder/'flow.json'
    data = json.loads(path.read_text())
    data.update(changes)
    atomic_json(path, data)


def test_renaming_preserves_ids_cache_and_history(workspace):
    (workspace/'data.txt').write_text('history survives folder rename')
    before = cli(workspace, 'run', '--task', 'ai_flow02', '--input', 'data.txt', '--output', 'out')['result']
    folder = workspace/'我的 講義整理'
    (workspace/'ai_flow02').rename(folder)
    modify(folder, title='講義備份')
    after = cli(workspace, 'run', '--task', '講義備份', '--input', 'data.txt', '--output', 'out')['result']
    assert before['flow_id'] == after['flow_id']
    assert before['job_id'] == after['job_id']
    assert before['run_id'] != after['run_id']
    assert after['steps'][0]['status'] == 'cached'
    assert len(cli(workspace, 'history', '--task', folder.name)['result']) == 2


def test_legacy_subtitle_folder_renamed_without_changing_package(workspace):
    (workspace/'ai_flow01').rename(workspace/'ai_flow_ASR_video')
    cli(workspace, 'demo', '--task', 'ai_flow_ASR_video', '--output', 'demo')
    assert (workspace/'demo/artifacts/subtitles.srt').is_file()


def test_missing_id_is_generated_once_and_module_is_independent(workspace):
    folder = workspace/'ai_flow02'
    modify(folder, id=None, module='ai_flow02')
    def discover(_):
        t = Workspace(workspace).task(folder.name)
        return t.manifest.id, t.flow_id
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(discover, range(8)))
    assert len(set(results)) == 1
    assert results[0][0].startswith('workflow_')
    folder.rename(workspace/'備份工具')
    task = Workspace(workspace).task('備份工具')
    assert (task.manifest.id, task.flow_id) == results[0]
    assert cli(workspace, 'plan', '--task', '備份工具')['result']['ready_to_prepare']


def test_duplicate_id_and_ambiguous_titles_are_not_silent(workspace):
    shutil.copytree(workspace/'ai_flow02', workspace/'第二份')
    assert cli(workspace, 'list', ok=False)['error']['code'] == 'TASK_DUPLICATE'
    modify(workspace/'第二份', id='backup-copy', module='ai_flow02')
    assert cli(workspace, 'plan', '--task', '檔案複製驗證範例', ok=False)['error']['code'] == 'TASK_AMBIGUOUS'
    assert cli(workspace, 'plan', '--task', '第二份')['result']['ready_to_prepare']


@pytest.mark.parametrize('module', ['flow_base', '../bad', 'os.path', 'class'])
def test_invalid_module_is_rejected(workspace, module):
    modify(workspace/'ai_flow02', module=module)
    cli(workspace, 'list', ok=False)


def run_chat(workspace, folder, text, *args):
    env = dict(os.environ, PYTHONPATH=str(ROOT/'flow_base/src'), PYTHONDONTWRITEBYTECODE='1')
    return subprocess.run([sys.executable, '-m', 'flow_base', '--workspace', str(workspace),
                           'launch', '--folder', str(folder), *args], input=text,
                          capture_output=True, text=True, env=env, timeout=40)


def test_launch_prepares_only_selected_flow_and_accepts_input_immediately(workspace):
    folder = workspace/'檔案 備份'
    (workspace/'ai_flow02').rename(folder)
    (workspace/'講義.txt').write_text('只啟動指定流程')
    result = run_chat(workspace, folder, '/input 講義.txt\n/run\n/quit\n', '--offline')
    assert result.returncode == 0, result.stdout + result.stderr
    assert '目前流程：檔案複製驗證範例' in result.stdout
    assert list((folder/'outputs').rglob('result.bin'))[0].read_text() == '只啟動指定流程'
    assert not (workspace/'share_states/environments').exists()
    states = [json.loads(p.read_text()) for p in (workspace/'share_states/launcher').glob('*.json')]
    assert states[-1]['status'] == 'ready'


def test_preparation_failure_then_retry_records_both_states(workspace):
    folder = workspace/'ai_flow02'
    wheel = next(folder.glob('*.whl'))
    original = wheel.read_bytes()
    wheel.write_bytes(b'broken')
    first = run_chat(workspace, folder, '', '--prepare-only')
    assert first.returncode == 2 and 'TASK_INTEGRITY' in first.stdout
    wheel.write_bytes(original)
    second = run_chat(workspace, folder, '', '--prepare-only', '--offline')
    assert second.returncode == 0, second.stdout + second.stderr
    states = [json.loads(p.read_text()) for p in (workspace/'share_states/launcher').glob('*.json')]
    assert sorted(s['status'] for s in states) == ['failed', 'ready']
    assert not cli(workspace, 'history')['result']  # installation is not a task run


def test_task_wrapper_delegates_with_actual_path_and_preserves_arguments(tmp_path):
    folder = tmp_path/'工作區 空格'/'字幕 新名稱'
    folder.mkdir(parents=True)
    base = folder.parent/'flow_base'
    base.mkdir()
    profile.generate_task(folder)
    (base/'run_app.sh').write_text('''#!/bin/sh
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
printf '%s\\n' "$@" > "$HERE/received.txt"
exit 19
''')
    for name in ('run_app.sh', 'run_app.command'):
        p = folder/name
        subprocess.run(['sh', '-n', str(p)], check=True)
        assert p.stat().st_mode & 0o111
        r = subprocess.run(['sh', str(p), '--config', '我的 設定.json'], cwd=tmp_path,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
        assert r.returncode == 19
        assert (base/'received.txt').read_text().splitlines() == ['launch', '--folder', str(folder), '--config', '我的 設定.json']
    bat = (folder/'run_app.bat').read_bytes()
    assert bat.isascii() and b'\r\n' in bat
    assert b'--folder "%~dp0."' in bat
    assert b'call ' not in bat.lower()  # avoid second CMD expansion of path characters


def test_missing_base_explains_layout_without_creating_private_environment(tmp_path):
    folder = tmp_path/'字幕'
    folder.mkdir()
    profile.generate_task(folder)
    r = subprocess.run(['sh', str(folder/'run_app.sh')], stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=10)
    assert r.returncode == 2 and '同一層' in r.stderr
    assert not (folder/'.venv').exists()


def test_launch_cannot_select_outside_workspace(workspace):
    result = run_chat(workspace, workspace.parent, '', '--prepare-only')
    assert result.returncode == 2 and 'TASK_FOLDER' in result.stdout


def test_explicit_config_is_preserved_without_relaxing_intent_allowlist(workspace):
    folder = workspace/'ai_flow02'
    (workspace/'data.txt').write_text('trusted configuration')
    config = workspace/'設定.json'
    atomic_json(config, {'id': 'my-backup'})  # trusted config, not a natural-language override
    result = run_chat(workspace, folder, '/input data.txt\n/run\n/quit\n', '--config', str(config))
    assert result.returncode == 0 and 'INTENT_PARAMETER' not in result.stdout, result.stdout
    assert list((workspace/'ai_flow02/outputs').rglob('result.bin'))


def test_selected_flow_understands_request_without_repeating_its_name(workspace):
    (workspace/'data.txt').write_text('implicit selected workflow')
    result = run_chat(workspace, workspace/'ai_flow02', '處理「data.txt」存到「完成」\n/run\n/quit\n')
    assert result.returncode == 0, result.stdout
    assert (workspace/'完成/artifacts/result.bin').read_text() == 'implicit selected workflow'


def test_flow_local_settings_are_loaded_and_explicit_config_wins(workspace):
    folder = workspace/'ai_flow02'
    atomic_json(folder/'settings.json', {'id': 'local-backup'})
    assert cli(workspace, 'plan', '--task', folder.name)['result']['workflow'] == 'local-backup'
    atomic_json(workspace/'override.json', {'id': 'override-backup'})
    assert cli(workspace, 'plan', '--task', folder.name, '--config', workspace/'override.json')['result']['workflow'] == 'override-backup'
