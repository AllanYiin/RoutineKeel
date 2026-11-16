import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
import pytest
from flow_base.runtime import FlowError, read_json
from flow_base.workspace import Workspace
from test_workspace import workspace, cli, ROOT


def test_recovery_launch_skips_model_preparation(workspace):
    result=cli(workspace,'launch','--folder',workspace/'ai_flow01','--action','recover-run',ok=False)
    assert result['error']['code']=='RECOVERY_SELECTION'
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


def test_generated_recovery_entry_delegates_without_new_installer(tmp_path):
    spec=importlib.util.spec_from_file_location('recovery_launcher',ROOT/'base/scripts/flow_base_profile.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    (tmp_path/'flow.json').write_text(json.dumps({'actions':['restore-job'],'recovery_action':'restore-job'}))
    module.generate_task(tmp_path)
    bat=(tmp_path/'recover_failed_run.bat').read_bytes()
    assert bat.isascii() and b'\r\n' in bat and b'--action "restore-job"' in bat
    # Replace only the delegated entry with a harmless argument recorder.
    (tmp_path/'run_app.sh').write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nexit 7\n')
    p=subprocess.run(['sh',str(tmp_path/'recover_failed_run.sh'),'--params','a b.json'],
                     input='',capture_output=True,text=True)
    assert p.returncode==7
    assert p.stdout.splitlines()==['--action','restore-job','--params','a b.json']
