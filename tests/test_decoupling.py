"""The host knows contracts and capabilities; it must not depend on the subtitle plugin."""
import ast
import json
import shutil
import zipfile
from pathlib import Path

import pytest

from flow_base.intent import model_intent
from flow_base.runtime import FlowError
from flow_base.workspace import Workspace
from test_workspace import cli, workspace

ROOT = Path(__file__).resolve().parents[1]


def test_base_wheel_has_no_domain_code_or_dependencies():
    wheel = next((ROOT/'dist/workspace/flow_base').glob('*.whl'))
    with zipfile.ZipFile(wheel) as archive:
        assert all(not name.startswith(('ai_flow01/', 'ai_flow02/')) for name in archive.namelist())
        metadata = archive.read(next(n for n in archive.namelist() if n.endswith('/METADATA'))).decode()
        dependencies = '\n'.join(s for s in metadata.splitlines() if s.startswith('Requires-Dist:')).lower()
        assert not any(n in dependencies for n in ('whisper', 'ctranslate', 'onnx', 'torch', 'opencc'))
        for name in archive.namelist():
            if not name.endswith('.py'): continue
            source = archive.read(name).decode()
            assert not any(n in source for n in ('large-v3', 'review-approve', 'speech.vad', 'subtitles.srt'))
            for node in ast.walk(ast.parse(source)):
                modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
                assert not any(m.split('.')[0] in {'ai_flow01','ai_flow02','faster_whisper','ctranslate2'} for m in modules)


def test_remove_subtitle_package_leaves_copy_workflow_operational(workspace):
    shutil.rmtree(workspace/'ai_flow01')
    (workspace/'input.txt').write_text('independent flow')
    result = cli(workspace, 'run', '--task', '檔案複製驗證範例', '--input', 'input.txt', '--output', 'out')['result']
    assert result['status'] == 'complete'
    assert (workspace/'out/artifacts/result.bin').read_text() == 'independent flow'
    assert not (workspace/'share_states/environments').exists()


def test_forbidden_flow_stops_before_external_router(workspace):
    ws = Workspace(workspace)
    def forbidden_transport(*args, **kwargs):
        pytest.fail('provider must not be called')
    with pytest.raises(FlowError) as error:
        model_intent(ws, '字幕', selected=ws.task('ai_flow01'), transport=forbidden_transport)
    assert error.value.code == 'LOCAL_LLM_FORBIDDEN'


def test_generic_action_requires_manifest_declaration(workspace):
    ws = Workspace(workspace)
    with pytest.raises(FlowError) as error:
        ws.task('ai_flow02').invoke_action(ws.runtime, 'review-approve', {})
    assert error.value.code == 'TASK_ACTION_UNKNOWN'


def test_subtitle_action_requires_explicit_human_confirmation(workspace):
    params=workspace/'approve.json'
    params.write_text(json.dumps({'output':'out','reviewer':'fixture','reviewed_issues':0,'confirm_listened':False}))
    error=cli(workspace,'action','--task','ai_flow01','--name','review-approve','--params',params,ok=False)
    assert error['error']['code'] == 'HUMAN_APPROVAL_REQUIRED'
    failures = list((workspace/'share_logs').glob('flow_*/run_*/failure.json'))
    assert len(failures) == 1


def test_review_packet_action_has_distinct_run_ids(workspace):
    out = workspace/'out'; out.mkdir()
    (out/'review_packet.json').write_text(json.dumps({'segments':[]}))
    params=workspace/'packet.json';params.write_text(json.dumps({'output':'out'}))
    a = cli(workspace,'action','--task','ai_flow01','--name','review-packet','--params',params)['result']
    b = cli(workspace,'action','--task','ai_flow01','--name','review-packet','--params',params)['result']
    assert a['flow_id'] == b['flow_id'] and a['run_id'] != b['run_id']
    assert a['result']['llm_calls'] == 0
