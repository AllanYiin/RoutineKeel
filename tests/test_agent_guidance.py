import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('agent_guidance',ROOT/'tools/agent_guidance.py')
guide=importlib.util.module_from_spec(spec);spec.loader.exec_module(guide)


def test_guidance_keeps_domain_policy_out_of_base(tmp_path):
    base=tmp_path/'flow_base';base.mkdir()
    flow=tmp_path/'自訂 中文流程';flow.mkdir()
    guide.write_workspace_guide(tmp_path)
    guide.write_package_guide(base,ROOT/'routinekeel/flow_base')
    guide.write_package_guide(flow,ROOT/'routinekeel/flows/課程影片產生字幕')
    common=(base/'AGENTS.md').read_text()
    domain=(flow/'AGENTS.md').read_text()
    assert 'catalog' in common and 'plan' in common and 'run' in common
    assert 'large-v3' not in common and 'review-approve' not in common
    assert 'large-v3' in domain and 'REVIEW_REQUIRED' in domain
    assert '../flow_base/AGENTS.md' in domain
    assert 'flow_base/AGENTS.md' in (tmp_path/'AGENTS.md').read_text()


def test_generic_flow_gets_guide_without_domain_source(tmp_path):
    guide.write_package_guide(tmp_path,ROOT/'routinekeel/flows/檔案複製範例')
    text=(tmp_path/'AGENTS.md').read_text()
    assert '../flow_base/AGENTS.md' in text and 'large-v3' not in text


def test_subtitle_release_ships_the_required_skill_at_the_documented_path():
    name = '課程影片產生字幕'
    relative = 'skills/video-subtitle-creator.skill'
    source = ROOT / 'routinekeel' / 'flows' / name
    installed = ROOT / 'dist' / 'workspace' / 'workflows' / ('FLOW_' + name)
    bundle = (source / relative).read_bytes()
    assert (installed / relative).read_bytes() == bundle
    release = next((ROOT / 'dist' / 'release').glob('flow_subtitles-*.zip'))
    with ZipFile(release) as archive:
        assert archive.read(f'workflows/FLOW_{name}/{relative}') == bundle
        instructions = archive.read(f'workflows/FLOW_{name}/AGENTS.md').decode('utf-8')
        assert f']({relative})' in instructions
        assert '必須先開啟' in instructions
    with ZipFile(installed / relative) as skill:
        assert 'video-subtitle-creator/SKILL.md' in skill.namelist()


def test_agent_cli_from_task_directory_executes_selected_flow(tmp_path):
    task_dir=tmp_path/'中文 改名流程'
    shutil.copytree(ROOT/'dist/workspace/workflows/FLOW_檔案複製範例',task_dir)
    source=tmp_path/'input.txt';source.write_text('agent uses installed flow')
    output=tmp_path/'out'
    env=dict(os.environ,PYTHONPATH=str(ROOT/'routinekeel/flow_base/src'),PYTHONDONTWRITEBYTECODE='1')
    def call(*args):
        p=subprocess.run([sys.executable,'-m','flow_base','--workspace',str(tmp_path),*args],
             cwd=task_dir,env=env,text=True,capture_output=True,timeout=40)
        assert p.returncode==0,p.stdout+p.stderr
        return json.loads(p.stdout)['result']
    catalog=call('catalog');assert catalog['tasks'][0]['folder']==task_dir.name
    plan=call('plan','--task',task_dir.name,'--input',str(source),'--output',str(output))
    assert plan['blocked']==[]
    result=call('run','--task',task_dir.name,'--input',str(source),'--output',str(output))
    assert result['flow_id'] and result['run_id'] and result['steps'][0]['status']=='executed'
    assert (output/'artifacts/result.bin').read_text()==source.read_text()
