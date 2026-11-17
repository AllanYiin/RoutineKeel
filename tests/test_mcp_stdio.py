"""Official SDK clients call stateless queue tools across MCP reconnections."""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

pytest.importorskip('mcp')
from mcp import Client, StdioServerParameters

from flow_base.core.queue import QueueStore
from flow_base.core.service import WorkflowService
from flow_base.core.types import SubmitRequest
from flow_base.core.lifecycle import RunStore
from flow_base.runtime import atomic_json
from test_workspace import workspace, cli


def client_for(root, *, mode='auto'):
    return Client(StdioServerParameters(command=sys.executable,
        args=['-m', 'flow_base', '--workspace', str(root), 'mcp-stdio']), mode=mode)


async def call(client, name, **arguments):
    response = await client.call_tool(name, arguments)
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
                             'get_workflow_result', 'cancel_workflow'}
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
    worker = subprocess.Popen([sys.executable, '-m', 'flow_base', '--workspace', str(workspace),
                               'queue-worker', '--idle-timeout', '3', '--poll-interval', '0.1'],
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True)
    try:
        async def submit():
            async with client_for(workspace) as client:
                planned = await call(client, 'plan_workflow', workflow_id='ai_flow02',
                                     source=str(source), output=str(output))
                args = dict(workflow_id='ai_flow02', source=str(source), output=str(output),
                            idempotency_key='mcp-queued-fixture',
                            execution_spec_hash=planned['result']['execution_spec_hash'])
                first = await call(client, 'submit_workflow', **args)
                second = await call(client, 'submit_workflow', **args)
                assert first['result']['execution_id'] == second['result']['execution_id']
                assert first['result']['run_id'] is None
                return first['result']['execution_id']

        execution_id = asyncio.run(submit())
        assert execution_id.startswith('execution_')

        async def reconnect():
            async with client_for(workspace) as client:
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    response = await call(client, 'get_workflow_status', execution_id=execution_id)
                    if response['result']['status'] in {'succeeded', 'failed', 'interrupted'}:
                        break
                    await asyncio.sleep(0.1)
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
        if worker.poll() is None:
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
    store.claim(run_id, token='fixture-supervisor', pid=123, started_at=1.0)
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
            observed = subprocess.run(['netstat', '-ano', '-p', 'tcp'],
                                      capture_output=True, text=True, check=True)
            rows = [line for line in observed.stdout.splitlines()
                    if line.strip().split()[-1:] == [pid] and 'LISTEN' in line.upper()]
            assert rows == []

    asyncio.run(inspect())
