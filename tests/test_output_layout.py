import json
import shutil
from pathlib import Path

import pytest
from flow_base.engine import run_workflow
from flow_base.outputs import artifact_directory
from flow_base.runtime import atomic_json, sha256
from flow_base.workspace import Workspace
from test_workspace import cli


def test_default_cli_and_natural_language_use_same_task_area(workspace):
    source = workspace / '講義.txt'
    source.write_text('portable output')
    planned = cli(workspace, 'plan', '--task', 'ai_flow02', '--input', source)['result']
    routed = cli(workspace, 'ask', '複製「講義.txt」')['result']
    assert planned['output'] == routed['output']
    assert planned['output_selection'] == routed['plan']['output_selection'] == 'workflow_default'
    output = Path(planned['output'])
    assert output.parent == workspace / 'ai_flow02/outputs'
    assert not output.exists(), 'planning must not create delivery directories'
    first = cli(workspace, 'run', '--task', 'ai_flow02', '--input', source)['result']
    again = cli(workspace, 'run', '--task', 'ai_flow02', '--input', source)['result']
    assert (output / 'artifacts/result.bin').read_text() == source.read_text()
    assert (output / 'docs/run-summary.md').is_file()
    assert not (output / 'scripts').exists()
    assert not (workspace / 'share_states/outputs').exists()
    assert first['job_id'] == again['job_id'] and first['run_id'] != again['run_id']
    assert again['steps'][0]['status'] == 'cached'
    index = json.loads((output / 'deliverables.json').read_text())
    assert index['run_id'] == again['run_id']
    for row in index['files']:
        assert not Path(row['path']).is_absolute()
        assert sha256(output / row['path']) == row['sha256']


def test_default_survives_workspace_move(workspace):
    (workspace / 'a.txt').write_text('move')
    first = cli(workspace, 'run', '--task', 'ai_flow02', '--input', 'a.txt')['result']
    moved = workspace.parent / 'moved 中文'
    shutil.copytree(workspace, moved)
    second = cli(moved, 'run', '--task', 'ai_flow02', '--input', 'a.txt')['result']
    assert first['job_id'] == second['job_id']
    assert second['steps'][0]['status'] == 'cached'
    assert Path(second['output']).is_relative_to(moved / 'ai_flow02/outputs')


def test_renaming_flow_keeps_default_output_and_job_ownership(workspace):
    source = workspace / 'ai_flow02/input.txt'; source.write_text('rename flow')
    first = cli(workspace, 'run', '--task', 'ai_flow02', '--input', source)['result']
    renamed = workspace / '資料 整理'
    (workspace / 'ai_flow02').rename(renamed)
    second = cli(workspace, 'run', '--task', renamed.name, '--input', renamed / 'input.txt')['result']
    assert first['flow_id'] == second['flow_id'] and first['job_id'] == second['job_id']
    assert Path(first['output']).name == Path(second['output']).name
    assert second['steps'][0]['status'] == 'cached'


@pytest.mark.parametrize('relative', ['.', 'flow_base/out', '.venv/out', 'share_states/out',
    'share_logs/out', 'ai_flow02', 'ai_flow02/outputs', 'ai_flow02/scripts/generated',
    'ai_flow01/outputs/other-job'])
def test_protected_output_locations_are_rejected_before_execution(workspace, relative):
    (workspace / 'a.txt').write_text('keep')
    result = cli(workspace, 'run', '--task', 'ai_flow02', '--input', 'a.txt', '--output', relative, ok=False)
    assert result['error']['code'] == 'OUTPUT_PROTECTED'
    assert (workspace / 'a.txt').read_text() == 'keep'


def test_symlink_category_cannot_write_outside_delivery_area(workspace):
    (workspace / 'a.txt').write_text('keep')
    first = cli(workspace, 'run', '--task', 'ai_flow02', '--input', 'a.txt')['result']
    output = Path(first['output'])
    outside = workspace / 'outside'; outside.mkdir()
    shutil.rmtree(output / 'artifacts')
    (output / 'artifacts').symlink_to(outside, target_is_directory=True)
    result = cli(workspace, 'run', '--task', 'ai_flow02', '--input', 'a.txt', ok=False)
    assert result['error']['code'] == 'OUTPUT_LINK'
    assert not list(outside.iterdir())


def test_existing_flat_results_stay_flat_after_manifest_upgrade(workspace):
    path = workspace / 'ai_flow02/flow.json'
    manifest = json.loads(path.read_text()); manifest.pop('output_layout')
    atomic_json(path, manifest)
    (workspace / 'a.txt').write_text('legacy')
    first = cli(workspace, 'run', '--task', 'ai_flow02', '--input', 'a.txt', '--output', 'legacy')['result']
    report = workspace / 'legacy/report.json'
    data = json.loads(report.read_text()); data.pop('output_layout'); atomic_json(report, data)
    manifest['output_layout'] = 'categorized-v1'; atomic_json(path, manifest)
    again = cli(workspace, 'run', '--task', 'ai_flow02', '--input', 'a.txt', '--output', 'legacy')['result']
    assert again['output_layout'] == 'flat-v1'
    assert again['job_id'] == first['job_id']
    assert (workspace / 'legacy/result.bin').read_text() == 'legacy'
    assert not (workspace / 'legacy/artifacts').exists()


def test_changed_input_does_not_overwrite_existing_default_job(workspace):
    source = workspace / 'a.txt'; source.write_text('original')
    first = cli(workspace, 'run', '--task', 'ai_flow02', '--input', 'a.txt')['result']
    source.write_text('changed')
    result = cli(workspace, 'run', '--task', 'ai_flow02', '--input', 'a.txt', ok=False)
    assert result['error']['code'] == 'OUTPUT_EXISTS'
    assert (Path(first['output']) / 'artifacts/result.bin').read_text() == 'original'


def test_custom_flow_can_deliver_docs_and_scripts_without_executing_them(workspace):
    source = workspace / 'a.txt'; source.write_text('data')
    ws = Workspace(workspace); task = ws.task('ai_flow02'); adapter = task.load(ws.runtime)
    workflow = task.configuration(ws.runtime)
    def publish(terminal, output, workflow):
        from flow_base.runtime import atomic_bytes
        atomic_bytes(output / 'docs/explanation.md', b'how to use this output')
        atomic_bytes(output / 'scripts/helper.py', b'raise RuntimeError("must not execute")')
        return adapter.publish(terminal, output / 'artifacts', workflow)
    adapter.publish_outputs = publish
    report = run_workflow(ws.runtime, task, workflow, source)
    index = json.loads((Path(report['output']) / 'deliverables.json').read_text())
    assert {'docs', 'artifacts', 'scripts'} == {r['category'] for r in index['files']}


@pytest.mark.parametrize('layout', ['flat-v1', 'categorized-v1'])
def test_subtitle_packet_resolves_both_layouts(tmp_path, layout):
    from ai_flow01.adapter import Adapter
    from flow_base.runtime import Runtime
    runtime = Runtime(tmp_path / 'share_states')
    output = tmp_path / 'out'; output.mkdir()
    atomic_json(output / 'report.json', {'output_layout': layout})
    artifacts = artifact_directory(output)
    atomic_json(artifacts / 'review_packet.json', {'segments': []})
    result = Adapter().invoke_action(runtime, 'review-packet', {'output': str(output)})
    assert Path(result['packet']) == artifacts / 'review_packet.json'


def test_categorized_subtitle_review_updates_root_report_and_index(tmp_path, monkeypatch):
    from ai_flow01 import adapter as domain
    from flow_base.runtime import Runtime
    runtime = Runtime(tmp_path / 'share_states')
    audio = runtime.home / 'audio.wav'; audio.parent.mkdir(); audio.write_bytes(b'test audio')
    output = tmp_path / 'out'; artifacts = output / 'artifacts'
    atomic_json(artifacts / 'flow_context.json', {'profile': 'asr-cpu', 'model': 'whisper-large-v3',
        'source_audio': 'audio.wav', 'source_audio_sha256': sha256(audio)})
    atomic_json(output / 'report.json', {'output_layout': 'categorized-v1', 'flow_id': 'f',
        'run_id': 'r', 'job_id': 'j', 'status': 'REVIEW_REQUIRED'})
    monkeypatch.setattr(domain, 'configure_profile', lambda *a: None)
    monkeypatch.setattr(runtime, 'prepare_profile', lambda *a: None)
    monkeypatch.setattr(runtime, 'update_run', lambda *a, **kw: None)
    def worker(profile, request, **kwargs):
        assert request['output_dir'] == str(artifacts)
        atomic_json(artifacts / 'QA.json', {'decision': 'REVIEW_REQUIRED'})
        return {'decision': 'REVIEW_REQUIRED'}
    monkeypatch.setattr(runtime, 'worker', worker)
    domain.Adapter().review_operation(runtime, output, 'build')
    assert json.loads((output / 'report.json').read_text())['status'] == 'REVIEW_REQUIRED'
    assert 'artifacts/QA.json' in {r['path'] for r in json.loads((output / 'deliverables.json').read_text())['files']}


def test_source_archive_never_ships_task_outputs_or_shared_state(tmp_path, monkeypatch):
    import importlib.util
    import zipfile
    tools = Path(__file__).resolve().parents[1] / 'tools'
    monkeypatch.syspath_prepend(str(tools))
    spec = importlib.util.spec_from_file_location('release_for_output_test', tools / 'release.py')
    release = importlib.util.module_from_spec(spec); spec.loader.exec_module(release)
    project = tmp_path / 'project'
    for name in ['src/main.py', 'outputs/job/artifacts/private.txt',
                 'share_states/private.json', 'share_logs/private.log']:
        path = project / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('fixture')
    archive = tmp_path / 'source.zip'
    release.source_archive(archive, [(project, 'project/')])
    with zipfile.ZipFile(archive) as z:
        assert z.namelist() == ['project/src/main.py']
