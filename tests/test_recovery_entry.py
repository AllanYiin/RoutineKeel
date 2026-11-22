import importlib.util
import json
import os
import shutil
import subprocess
import sys
from contextlib import closing
from types import SimpleNamespace

import pytest
from flow_base.runtime import FlowError, read_json
from flow_base.workspace import Workspace
from test_workspace import ROOT


def test_recovery_launch_skips_model_preparation(workspace):
    # The interactive launch entry renders human-readable errors, unlike action.
    result = subprocess.run(
        [sys.executable, '-m', 'flow_base', '--workspace', str(workspace),
         'launch', '--folder', str(workspace/'ai_flow01'), '--action', 'recover-run'],
        env={**os.environ, 'PYTHONPATH': str(ROOT/'flow_base/src')},
        capture_output=True, text=True, timeout=40, check=False,
    )
    assert result.returncode != 0
    assert 'RECOVERY_SELECTION' in result.stdout
    assert '錯誤編號：' in result.stdout
    assert not (workspace/'share_states/environments').exists()
    assert not (workspace/'share_states/resources').exists()


@pytest.mark.parametrize('fail',[False,True])
def test_action_context_is_scoped_and_status_is_persisted(workspace,monkeypatch,fail):
    ws=Workspace(workspace);task=ws.task('ai_flow02');task.manifest.actions=['fixture-action']
    prior={'sentinel':True};ws.runtime.action_context=prior
    observed={}
    def action(runtime,name,params):
        observed.update(runtime.action_context)
        assert runtime.action_context['flow_id']==task.flow_id
        if fail:raise FlowError('FIXTURE','synthetic failure')
        return {'status':'REVIEW_REQUIRED'}
    monkeypatch.setattr(task,'load',lambda _:SimpleNamespace(invoke_action=action))
    if fail:
        with pytest.raises(FlowError):task.invoke_action(ws.runtime,'fixture-action',{})
    else:task.invoke_action(ws.runtime,'fixture-action',{})
    assert ws.runtime.action_context is prior
    state=read_json(observed['logdir']/'run.json')
    assert state['status']==('failed' if fail else 'REVIEW_REQUIRED')
    with closing(ws.runtime.connect()) as db:
        attempt=db.execute('SELECT attempt_id,status,error_id FROM step_attempts WHERE run_id=?',
                           (observed['run_id'],)).fetchone()
    assert attempt['status']==('failed' if fail else 'succeeded')
    assert bool(attempt['error_id']) == fail
    events=ws.runtime.events(observed['run_id'],limit=100)['events']
    assert any(event['name']=='step.started' and event['step_attempt_id']==attempt['attempt_id']
               for event in events)
    terminal = 'step.failed' if fail else 'step.finished'
    assert any(event['name']==terminal and event['step_attempt_id']==attempt['attempt_id']
               for event in events)


@pytest.mark.skipif(os.name == 'nt' or shutil.which('sh') is None, reason='POSIX sh is required')
def test_generated_recovery_entry_delegates_without_new_installer(tmp_path):
    spec=importlib.util.spec_from_file_location('recovery_launcher',ROOT/'flow_base/scripts/flow_base_profile.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    (tmp_path/'flow.json').write_text(json.dumps({'actions':['restore-job'],'recovery_action':'restore-job'}))
    module.generate_task(tmp_path)
    bat=(tmp_path/'recover_failed_run.bat').read_bytes()
    assert bat.isascii() and b'\r\n' in bat and b'--action "restore-job"' in bat
    # Replace only the delegated entry with a harmless argument recorder.
    (tmp_path/'run_app.sh').write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 7\n')
    p=subprocess.run(['sh',str(tmp_path/'recover_failed_run.sh'),'--params','a b.json'],
                     input='',capture_output=True,text=True, check=False)
    assert p.returncode==7
    assert p.stdout.splitlines()==['--action','restore-job','--params','a b.json']
