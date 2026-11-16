import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from flow_base.presentation import ACTIVE, Reporter
from flow_base.runtime import Runtime, FlowError, run_process
from flow_base.usage import UsageMeter, normalize_usage
from test_workspace import workspace, ROOT


def test_real_progress_and_terminal_state_are_persisted(tmp_path,capsys):
    reporter=Reporter('測試流程','flow_test','run_test',tmp_path,2)
    reporter.start();reporter.enter(SimpleNamespace(id='one',handler='unit.work'),1,'長時間任務')
    reporter.progress({'event':'progress','completed':3,'total':10,'unit':'區段','label':'處理'})
    assert '30.0%' in capsys.readouterr().err
    state=json.loads((tmp_path/'live_progress.json').read_text());assert state['progress']['completed']==3
    reporter.complete('cached')
    reporter.end({'status':'needs_review','output':'out'},{'label':'等待人工確認'})
    text=capsys.readouterr().err
    assert '快取命中' in text and '等待人工確認' in text and '已快取：0' in text
    assert '宿主對話用量：無法取得' in text
    assert json.loads((tmp_path/'live_progress.json').read_text())['event']=='workflow_finished'


def test_unknown_duration_never_invents_percentage(tmp_path,capsys):
    reporter=Reporter('測試','f','r',tmp_path,1)
    reporter.progress({'event':'heartbeat'})
    text=capsys.readouterr().err
    assert '尚無可計算百分比' in text and '%' not in text
    reporter.progress({'event':'progress','completed':11,'total':10})
    assert capsys.readouterr().err==''


def test_failure_is_not_reported_as_completion(tmp_path,capsys):
    r=Reporter('測試','f','r',tmp_path,1);r.start()
    r.token_receipt('request',pending=True)
    r.fail('PROCESS_FAILED','test failure')
    text=capsys.readouterr().err
    assert '工作流執行失敗' in text and '工作流處理結束' not in text
    assert '未提供／不完整' in text
    state=json.loads((tmp_path/'live_progress.json').read_text())
    assert state['event']=='workflow_failed' and state['token_usage']['output'] is None


def test_worker_forwards_only_structured_progress(tmp_path):
    received=[]
    code="import json,time;print('PRIVATE_TRANSCRIPT',flush=True);print(json.dumps({'event':'progress','completed':1,'total':2}),flush=True);time.sleep(.6);print(json.dumps({'event':'progress','completed':2,'total':2}),flush=True)"
    run_process([sys.executable,'-c',code],log=tmp_path/'worker.log',progress=received.append,timeout=5)
    assert [r['completed'] for r in received if r['event']=='progress']==[1,2]
    assert 'PRIVATE_TRANSCRIPT' not in json.dumps(received)


def test_progress_callback_does_not_disable_timeout(tmp_path):
    with pytest.raises(FlowError) as exc:
        run_process([sys.executable,'-c','import time;time.sleep(10)'],log=tmp_path/'worker.log',progress=lambda _:None,timeout=.1)
    assert exc.value.code=='PROCESS_TIMEOUT'


@pytest.mark.parametrize('usage',[
 {'prompt_tokens':100,'completion_tokens':20,'prompt_tokens_details':{'cached_tokens':40}},
 {'input_tokens':100,'output_tokens':20,'input_tokens_details':{'cached_tokens':40}},
])
def test_provider_cache_breakdown(usage):
    assert normalize_usage(usage)=={'input_total':100,'input_cached':40,'input_uncached':60,'output':20}


def test_missing_cache_detail_does_not_claim_zero():
    result=normalize_usage({'prompt_tokens':100,'completion_tokens':20})
    assert result['input_total']==100 and result['input_cached'] is result['input_uncached'] is None


def test_usage_deduplication_and_pending_requests():
    meter=UsageMeter();assert meter.summary()['input_total']==0
    meter.reserve('one');assert meter.summary()['output'] is None
    usage={'input_tokens':100,'output_tokens':20,'input_tokens_details':{'cached_tokens':40}}
    meter.settle('one',usage);meter.settle('one',usage)
    assert meter.summary()['input_total']==100 and meter.summary()['registered_requests']==1
    meter.reserve('two');result=meter.summary()
    assert result['input_total'] is None and result['known_subtotals']['input_total']==100
    assert result['host_agent_usage']['status']=='unavailable'


def test_impossible_provider_cache_receipt_is_not_used():
    result=normalize_usage({'input_tokens':10,'output_tokens':True,'input_tokens_details':{'cached_tokens':20}})
    assert result['input_cached'] is result['input_uncached'] is result['output'] is None


def test_runtime_receipts_are_bound_to_active_run(tmp_path):
    runtime=Runtime(tmp_path/'states');reporter=Reporter('test','f','r',tmp_path/'logs',1)
    token=ACTIVE.set(reporter)
    try:
        runtime.record_token_usage('one',pending=True)
        runtime.record_token_usage('one',{'input_tokens':5,'input_tokens_details':{'cached_tokens':0},'output_tokens':2})
        assert reporter.usage.summary()['input_uncached']==5
    finally:ACTIVE.reset(token)
    with pytest.raises(FlowError):runtime.record_token_usage('two',{})


def test_ledger_automatically_registers_provider_receipts(tmp_path):
    from flow_base.contracts import LLM
    from flow_base.llm import Ledger
    runtime=Runtime(tmp_path/'state');reporter=Reporter('test','f','r',tmp_path/'logs',1)
    token=ACTIVE.set(reporter)
    try:
        settings=LLM(enabled=True,model='fixture',pricing_verified=True,pricing_note='synthetic test',
                     input_usd_per_million=1,output_usd_per_million=1,budget_usd=1,max_calls=2)
        ledger=Ledger(runtime,'job',settings);rid=ledger.reserve('key',100)
        assert reporter.usage.summary()['output'] is None
        ledger.settle(rid,{'usage':{'prompt_tokens':100,'completion_tokens':20,'prompt_tokens_details':{'cached_tokens':80}}})
        usage=reporter.usage.summary()
        assert usage['input_cached']==80 and usage['input_uncached']==20 and usage['output']==20
    finally:ACTIVE.reset(token)


def test_packaged_cli_banners_preserve_stdout_json_and_cache_usage(workspace):
    (workspace/'in.txt').write_text('test')
    env=dict(os.environ,PYTHONPATH=str(ROOT/'flow_base/src'),PYTHONDONTWRITEBYTECODE='1')
    args=[sys.executable,'-m','flow_base','--workspace',str(workspace),'run','--task','ai_flow02',
          '--input','in.txt','--output','out']
    for iteration in range(2):
        p=subprocess.run(args,env=env,text=True,capture_output=True,timeout=30)
        assert p.returncode==0,p.stdout+p.stderr
        result=json.loads(p.stdout)['result']
        assert '工作流正式啟動' in p.stderr and '進入節點 1/1' in p.stderr and '工作流處理結束' in p.stderr
        assert result['token_usage']['input_cached']==0 and result['token_usage']['output']==0
        if iteration:assert '快取命中' in p.stderr
        logs=workspace/'share_logs'/result['flow_id']/result['run_id']
        events=[json.loads(line)['event'] for line in (logs/'events.jsonl').read_text().splitlines()]
        assert events.index('workflow_started')<events.index('step_started')<events.index('step_completed')<events.index('workflow_finished')
