"""Exercise the actual release archives after extracting into clean workspaces."""
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / 'dist/release'


def full_archive():
    return next(RELEASE.glob('RoutineKeel-workspace-*.zip'))


@pytest.fixture
def extracted(tmp_path):
    target = tmp_path / '安裝位置 with spaces'
    with zipfile.ZipFile(full_archive()) as archive:
        archive.extractall(target)
    return target / 'RoutineKeel'


def cli(workspace, *arguments, ok=True):
    wheel = next((workspace / 'flow_base').glob('flow_base-*.whl'))
    code = 'import sys;sys.path.insert(0,sys.argv.pop(1));from flow_base.cli import main;raise SystemExit(main())'
    command = [sys.executable, '-X', 'utf8', '-I', '-c', code, str(wheel), '--workspace', str(workspace), *map(str, arguments)]
    result = subprocess.run(command, cwd=workspace / 'workflow_studio', capture_output=True, text=True, encoding='utf-8', timeout=45)
    assert (result.returncode == 0) == ok, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_full_zip_contains_studio_runtime_and_two_reference_flows():
    with zipfile.ZipFile(full_archive()) as archive:
        assert archive.testzip() is None
        names = set(archive.namelist())
        assert all(n.startswith('RoutineKeel/') for n in names)
        for folder in ['flow_base', 'flow_base/share_states', 'flow_base/share_logs', 'blueprints', 'workflow_studio', 'workflows']:
            assert 'RoutineKeel/' + folder + '/' in names
        assert 'RoutineKeel/workflow_studio/studio.bat' in names
        assert {n for n in names if n.endswith('/flow.json')} == {
            'RoutineKeel/workflows/FLOW_課程影片產生字幕/flow.json',
            'RoutineKeel/workflows/FLOW_檔案複製範例/flow.json',
        }
        assert not any(part in {'.venv', '.studio-venv', 'outputs', '.git', '__pycache__'} for name in names for part in name.split('/'))
        assert not any(name.endswith(('/settings.json', '/router.json', '/state.sqlite3', '/model.bin')) for name in names)
        assert all(name.endswith('/') for name in names if '/share_states/' in name or '/share_logs/' in name)
        assert archive.getinfo('RoutineKeel/flow_base/start.sh').external_attr >> 16 & 0o111


def test_split_zips_overlay_same_relative_locations_as_full_bundle(tmp_path):
    reports = json.loads((RELEASE / 'versions.json').read_text(encoding='utf-8'))
    combined = full_archive().name
    with zipfile.ZipFile(full_archive()) as full:
        for report in reports:
            path = RELEASE / report['file']
            assert hashlib.sha256(path.read_bytes()).hexdigest() == report['sha256']
            if report['file'] == combined:
                continue
            with zipfile.ZipFile(path) as part:
                part.extractall(tmp_path)
                for item in part.infolist():
                    if not item.is_dir():
                        assert part.read(item) == full.read('RoutineKeel/' + item.filename)
    assert (tmp_path / 'flow_base/start.ps1').is_file()
    assert (tmp_path / 'workflows/FLOW_檔案複製範例/flow.json').is_file()
    assert (tmp_path / 'workflow_studio/studio.bat').is_file()


def test_packaged_studio_defaults_to_conversation_without_native_ui(extracted):
    wheel = next((extracted / 'workflow_studio').glob('workflow_studio-*.whl'))
    with zipfile.ZipFile(wheel) as package:
        assert not any(name.startswith('workflow_studio/ui/') for name in package.namelist())
    assert not list((extracted / 'workflow_studio').glob('open_studio.*'))
    code = '''
import importlib.abc, sys
sys.path.insert(0, sys.argv.pop(1))
class NoTk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname == "tkinter" or fullname.startswith("tkinter."):
            raise AssertionError("Tk must not be loaded")
sys.meta_path.insert(0, NoTk())
from workflow_studio.__main__ import main
raise SystemExit(main())
'''
    result = subprocess.run([sys.executable, '-X', 'utf8', '-I', '-c', code, str(wheel)],
                            cwd=extracted, capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['result']['mode'] == 'host_conversation'


def test_clean_extracted_wheel_catalog_plan_execution_and_cache(extracted):
    catalog = cli(extracted, 'catalog')['result']['tasks']
    assert {item['folder'] for item in catalog} == {'FLOW_課程影片產生字幕', 'FLOW_檔案複製範例'}
    source = extracted / 'workflows/FLOW_檔案複製範例/測試 input.txt'
    source.write_text('實際發行包：解壓、計畫、執行、快取。\n', encoding='utf-8')
    plan = cli(extracted, 'plan', '--task', 'ai_flow02', '--input', source)['result']
    assert plan['blocked'] == []
    output = Path(plan['output'])
    assert output.parent == source.parent / 'outputs'
    first = cli(extracted, 'run', '--task', 'ai_flow02', '--input', source)['result']
    second = cli(extracted, 'run', '--task', 'ai_flow02', '--input', source)['result']
    assert first['steps'][0]['status'] == 'executed'
    assert second['steps'][0]['status'] == 'cached'
    assert first['job_id'] == second['job_id']
    assert (output / 'artifacts/result.bin').read_bytes() == source.read_bytes()
    assert (extracted / 'flow_base/share_states/state.sqlite3').is_file()
    assert (extracted / 'flow_base/share_logs' / first['flow_id'] / first['run_id'] / 'report.json').is_file()
    assert not (extracted / 'share_states').exists()
    assert not (extracted / 'share_logs').exists()


@pytest.mark.parametrize('relative', [
    'workflows', 'flow_base/share_states/job', 'flow_base/share_logs/job',
    'blueprints/job', 'workflow_studio/job',
    'workflows/FLOW_課程影片產生字幕/outputs/job',
])
def test_clean_release_protects_shared_directories_and_other_flows(extracted, relative):
    source = extracted / 'input.txt'
    source.write_text('do not overwrite other workflows', encoding='utf-8')
    result = cli(extracted, 'plan', '--task', 'ai_flow02', '--input', source,
                 '--output', extracted / relative, ok=False)
    assert result['error']['code'] == 'OUTPUT_PROTECTED'


def test_nested_launch_folder_must_belong_to_workflows(extracted):
    from flow_base.runtime import FlowError
    from flow_base.workspace import Workspace
    ws = Workspace(extracted)
    assert ws.task_at(extracted / 'workflows/FLOW_檔案複製範例').manifest.id == 'ai_flow02'
    with pytest.raises(FlowError, match='流程目錄'):
        ws.task_at(extracted / 'workflow_studio')


@pytest.mark.parametrize('nested', [False, True])
def test_subtitle_review_resolves_relative_output_and_import_at_workspace(tmp_path, monkeypatch, nested):
    from ai_flow01.adapter import Adapter
    from flow_base.runtime import Runtime
    if nested:
        (tmp_path / 'workflows').mkdir()
    home = tmp_path / ('flow_base/share_states' if nested else 'share_states')
    runtime = Runtime(home)
    output = 'workflows/FLOW_字幕/outputs/job' if nested else '字幕/outputs/job'
    (tmp_path / output).mkdir(parents=True)
    adapter = Adapter()
    observed = {}

    def review(runtime, directory, operation, **parameters):
        observed.update(directory=directory, operation=operation, parameters=parameters)
        return {'status': 'REVIEW_REQUIRED'}

    monkeypatch.setattr(adapter, 'review_operation', review)
    adapter.invoke_action(runtime, 'review-import', {'output': output, 'csv_path': '填答.csv'})
    assert observed['directory'] == tmp_path / output
    assert observed['parameters']['csv_path'] == str(tmp_path / '填答.csv')
