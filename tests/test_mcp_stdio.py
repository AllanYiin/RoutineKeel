"""Official SDK clients call stateless queue tools across MCP reconnections."""
import asyncio
import json
import os
import shutil
import subprocess
import sys

import pytest

pytest.importorskip('mcp')
from flow_base.core.lifecycle import RunStore
from flow_base.core.queue import QueueStore
from flow_base.core.service import WorkflowService
from flow_base.core.types import SubmitRequest
from flow_base.runtime import atomic_json
from mcp import Client, StdioServerParameters
from test_workspace import cli


def client_for(root, *, mode='auto'):
    host_env = {name:os.environ[name] for name in ("ROUTINEKEEL_HOST_CATALOG","ROUTINEKEEL_STUDIO_PYTHON") if name in os.environ}
    return Client(StdioServerParameters(command=sys.executable,
        args=['-m', 'flow_base', '--workspace', str(root), 'mcp-stdio'],env=host_env), mode=mode)


def test_real_stdio_interview_resumes_and_keeps_host_answers_unconfirmed(tmp_path):
    async def exercise():
        async with client_for(tmp_path) as client:
            state = (await call(client,'studio_create_project',name='週報',task='整理週報'))['result']
            question = (await call(client,'studio_next_question',project_id=state['project_id']))['result']
            assert question['text'] and question['free_text']
            params = {'project_id': state['project_id'],'expected_revision': state['revision'],
                          'request_id': 'pilot','domain': 'goal','value': '整理週報','evidence_refs': ['對話草稿'],
                          'confidence': 0.8}
            denied = await call(client,'studio_submit_answer',**params,status='confirmed')
            assert denied['error']['code'] == 'USER_CONFIRMATION_REQUIRED'
            saved = (await call(client,'studio_submit_answer',**params))['result']
            assert saved['coverage']['goal']['status'] == 'inferred'
            shown = (await call(client,'studio_next_question',project_id=state['project_id']))['result']
            saved = (await call(client,'studio_record_reply',project_id=state['project_id'],domain='goal',
                                question_id=shown['question_id'],expected_revision=shown['revision'],
                                request_id='chat-2',user_text='整理每週的銷售報表'))['result']
        async with client_for(tmp_path) as client:
            reopened = (await call(client,'studio_get_project',project_id=state['project_id']))['result']
            assert reopened == saved and reopened['coverage']['goal']['origin'] == 'desktop_conversation'
            assert reopened['coverage']['goal']['value'] == '整理每週的銷售報表'
            batch = (await call(client, "studio_next_questions", project_id=state["project_id"],
                                domains=["inputs", "outputs", "review"]))["result"]
            answers = {"inputs": "銷售 CSV", "outputs": "週報 PDF"}
            submitted = (await call(
                client, "studio_record_replies", project_id=state["project_id"],
                expected_revision=batch["revision"], request_id="chat-3",
                user_text="輸入銷售 CSV，交付週報 PDF",
                replies=[
                    {"domain": q["domain"], "question_id": q["question_id"],
                     "answer_text": answers[q["domain"]]}
                    for q in batch["questions"] if q["domain"] in answers
                ],
            ))["result"]
            assert submitted["revision"] == batch["revision"] + 1
            assert submitted["coverage"]["inputs"]["value"] == "銷售 CSV"
            assert submitted["coverage"]["outputs"]["value"] == "週報 PDF"
            assert submitted["coverage"]["review"]["status"] == "unknown"
    asyncio.run(exercise())


async def call(client, tool_name, **arguments):
    response = await client.call_tool(tool_name, arguments)
    assert not response.is_error, response.content
    return json.loads(response.content[0].text)


def test_stdio_negotiation_queue_tool_set_and_path_boundary(workspace):
    source = workspace / 'input.txt'
    source.write_text('mcp input', encoding='utf-8')
    outside = workspace.parent / 'PRIVATE_SECRET_MARKER.txt'
    outside.write_text('private', encoding='utf-8')

    async def exercise(mode):
        async with client_for(workspace, mode=mode) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert names == {'list_workflows', 'describe_workflow', 'plan_workflow',
                             'submit_workflow', 'get_workflow_status',
                             'get_workflow_result', 'cancel_workflow', 'preview_workflow_update',
                             'update_workflow', 'rollback_workflow_update', 'recover_workflow_update',
                             'install_workflow', 'blueprint_task',
                             'studio_create_project', 'studio_list_projects', 'studio_get_project',
                             'studio_update_project', 'studio_archive_project', 'studio_restore_project',
                             'studio_next_question', 'studio_submit_answer', 'studio_pause_interview',
                             'studio_resume_interview', 'studio_preview_source', 'studio_start_interview',
                             'studio_record_reply', 'studio_preview_blueprint', 'studio_export_blueprint',
                             'studio_next_questions', 'studio_record_replies',
                             'studio_extract_pattern', 'studio_get_pattern_extraction',
                             'studio_export_pattern_extraction',
                             'studio_qualify_pattern', 'studio_get_pattern_trial',
                             'studio_generalize_patterns',
                             'studio_recommend_patterns', 'studio_decide_pattern',
                             'studio_inspect_pattern', 'studio_configure_patterns',
                             'studio_compose_pattern',
                             'resolve_blueprint_capabilities','workflow_quality','workflow_evolution','agent_workflow'}
            forbidden = await call(client, 'submit_workflow', workflow_id='ai_flow02',
                                   source=str(outside))
            assert forbidden['error']['code'] == 'PERMISSION_DENIED'
            assert 'PRIVATE_SECRET_MARKER' not in json.dumps(forbidden)
            unknown = await call(client, 'get_workflow_status', execution_id='execution_' + 'a' * 32)
            assert unknown['error']['code'] == 'EXECUTION_UNKNOWN'
            planned = await call(client, 'plan_workflow', workflow_id='ai_flow02',
                                 source=str(source), output=str(workspace / 'result'), offline=True)
            assert planned['ok'] and planned['result']['execution_spec_hash']
            return planned['result']['plan']

    current = asyncio.run(exercise('auto'))
    assert not (workspace / 'share_states').exists()
    assert current['workflow'] == cli(workspace, 'plan', '--task', 'ai_flow02',
        '--input', source, '--output', workspace / 'result', '--offline')['result']['workflow']
    legacy = asyncio.run(exercise('legacy'))
    assert legacy['workflow'] == current['workflow']


def test_client_exit_reconnects_to_independent_worker_by_execution_id(workspace):
    source = workspace / 'input.txt'
    source.write_text('queued result after client exit', encoding='utf-8')
    output = workspace / 'result'
    worker = None
    try:
        async def submit():
            async with client_for(workspace) as client:
                planned = await call(client, 'plan_workflow', workflow_id='ai_flow02',
                                     source=str(source), output=str(output))
                args = {'workflow_id': 'ai_flow02', 'source': str(source), 'output': str(output),
                            'idempotency_key': 'mcp-queued-fixture',
                            'execution_spec_hash': planned['result']['execution_spec_hash']}
                first = await call(client, 'submit_workflow', **args)
                second = await call(client, 'submit_workflow', **args)
                assert first['result']['execution_id'] == second['result']['execution_id']
                assert first['result']['run_id'] is None
                return first['result']['execution_id']

        execution_id = asyncio.run(submit())
        assert execution_id.startswith('execution_')
        # The first client is now gone and the job is durably queued. Start the independent worker here.
        worker = subprocess.Popen([sys.executable, '-m','flow_base','--workspace',str(workspace),
                                   'queue-worker','--once'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE,text=True)

        async def reconnect():
            async with client_for(workspace) as client:
                first = await call(client,'get_workflow_status',execution_id=execution_id)
                assert first['result']['execution_id'] == execution_id
                # --once exits after recording the terminal queue state. Cold environment
                # preparation is not a 20-second service contract.
                await asyncio.to_thread(worker.wait, timeout=120)
                response = await call(client, 'get_workflow_status', execution_id=execution_id)
                assert response['result']['status'] == 'succeeded'
                result = await call(client, 'get_workflow_result', execution_id=execution_id)
                assert result['result']['result_available']
                assert result['result']['run_id'] == response['result']['run_id']
                assert any(item['path'] == 'artifacts/result.bin'
                           for item in result['result']['artifacts'])
                return response['result']

        status = asyncio.run(reconnect())
        assert status['run_id'] == WorkflowService(workspace).get_workflow_status(execution_id)['run_id']
        assert (output / 'artifacts/result.bin').read_text(encoding='utf-8') == source.read_text(encoding='utf-8')
        stdout, stderr = worker.communicate(timeout=12)
        assert worker.returncode == 0, stdout + stderr
    finally:
        if worker is not None and worker.poll() is None:
            worker.terminate()
            worker.communicate(timeout=5)


def test_queued_cancel_and_active_cancel_request(workspace):
    source = workspace / 'input.txt'
    source.write_text('cancel input', encoding='utf-8')
    service = WorkflowService(workspace)
    queued = service.submit_workflow(SubmitRequest(task='ai_flow02', source=source,
        output=workspace / 'queued-result')).execution_id

    async def cancel_queued():
        async with client_for(workspace) as client:
            response = await call(client, 'cancel_workflow', execution_id=queued)
            assert response['result']['status'] == 'cancelled'
            assert response['result']['confirmed_stopped']
            result = await call(client, 'get_workflow_result', execution_id=queued)
            assert not result['result']['result_available']
    asyncio.run(cancel_queued())

    active = service.submit_workflow(SubmitRequest(task='ai_flow02', source=source,
        output=workspace / 'active-result')).execution_id
    queue = QueueStore(service.workspace.runtime)
    item = queue.get(active)
    token = 'fixture-worker'
    assert queue.claim(active, token)
    service.workspace.task('ai_flow02')
    run_id = service.workspace.runtime.start_run(item['flow_id'], source,
                                                 workspace / 'active-result')
    store = RunStore(service.workspace.runtime)
    store.transition(run_id, 'created', 'queued')
    from flow_base.execution.supervisor import process_started_at
    pid = os.getpid()
    store.claim(run_id, token='fixture-supervisor', pid=pid, started_at=process_started_at(pid))
    queue.mark_running(active, token, run_id)
    atomic_json(service.workspace.runtime.logs / item['flow_id'] / run_id / 'live_progress.json',
                {'step_id': 'copy', 'progress': {'completed': 126, 'total': 391,
                                               'label': 'PRIVATE_PROGRESS_MARKER'}})
    visible = service.get_workflow_status(active)
    assert visible['progress'] == {'completed': 126, 'total': 391}
    assert 'PRIVATE_PROGRESS_MARKER' not in json.dumps(visible)

    async def cancel_active():
        async with client_for(workspace) as client:
            response = await call(client, 'cancel_workflow', execution_id=active)
            assert response['result']['status'] == 'cancel_requested'
            assert response['result']['confirmed_stopped'] is False
    asyncio.run(cancel_active())
    store.transition(run_id, 'cancelling', 'cancelled')
    assert service.get_workflow_status(active)['status'] == 'cancelled'


def test_submit_before_worker_exists_stays_queued_across_client_restart(workspace):
    source = workspace / 'input.txt'
    source.write_text('worker starts later', encoding='utf-8')

    async def submit():
        async with client_for(workspace) as client:
            response = await call(client, 'submit_workflow', workflow_id='ai_flow02',
                                  source=str(source), output=str(workspace / 'result'))
            return response['result']['execution_id']

    execution_id = asyncio.run(submit())
    assert WorkflowService(workspace).get_workflow_status(execution_id)['status'] == 'queued'
    completed = cli(workspace, 'queue-worker', '--once', '--poll-interval', '0.1')['result']
    assert completed['processed'] == 1

    async def reconnect():
        async with client_for(workspace) as client:
            response = await call(client, 'get_workflow_result', execution_id=execution_id)
            assert response['result']['status'] == 'succeeded'
            assert response['result']['result_available']

    asyncio.run(reconnect())


@pytest.mark.skipif(os.name != 'nt' or shutil.which('netstat') is None,
                    reason='Windows netstat is required for process-level port observation')
def test_mcp_stdio_process_has_no_listening_tcp_port(workspace):
    pidfile = workspace / 'mcp-server.pid'
    script = ('import os,sys,pathlib;'
              'pathlib.Path(sys.argv[1]).write_text(str(os.getpid()));'
              'from flow_base.cli import main;'
              'raise SystemExit(main(sys.argv[2:]))')

    async def inspect():
        params = StdioServerParameters(command=sys.executable,
            args=['-c', script, str(pidfile), '--workspace', str(workspace), 'mcp-stdio'])
        async with Client(params) as client:
            await client.list_tools()
            pid = pidfile.read_text(encoding='utf-8').strip()
            observed = await asyncio.to_thread(subprocess.run, ['netstat', '-ano', '-p', 'tcp'],
                                      capture_output=True, text=True, check=True)
            rows = [line for line in observed.stdout.splitlines()
                    if line.strip().split()[-1:] == [pid] and 'LISTEN' in line.upper()]
            assert rows == []

    asyncio.run(inspect())
