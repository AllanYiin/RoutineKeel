"""MCP and CLI query the same durable execution identity."""
import asyncio
import json
import sys

import pytest

pytest.importorskip('mcp')
from mcp import Client, StdioServerParameters

from test_workspace import workspace, cli


def test_stdio_query_matches_cli_queue_status_and_result(workspace):
    source = workspace / 'input.txt'
    source.write_text('protocol parity', encoding='utf-8')
    submitted = cli(workspace, 'submit', '--task', 'ai_flow02', '--input', source,
                    '--output', workspace / 'result')['result']
    execution_id = submitted['execution_id']
    assert submitted['status'] == 'queued' and submitted['run_id'] is None
    cli(workspace, 'queue-worker', '--once', '--poll-interval', '0.1')
    cli_status = cli(workspace, 'execution-status', execution_id)['result']
    cli_result = cli(workspace, 'execution-result', execution_id)['result']

    async def check():
        params = StdioServerParameters(command=sys.executable,
            args=['-m', 'flow_base', '--workspace', str(workspace), 'mcp-stdio'])
        async with Client(params) as client:
            result = await client.call_tool('get_workflow_status', {'execution_id': execution_id})
            assert not result.is_error
            status = json.loads(result.content[0].text)
            assert status['ok']
            assert status['result']['run_id'] == cli_status['run_id']
            assert status['result']['status'] == cli_status['status'] == 'succeeded'
            result = await client.call_tool('get_workflow_result', {'execution_id': execution_id})
            assert not result.is_error
            result_data = json.loads(result.content[0].text)
            assert result_data['result']['run_id'] == cli_result['run_id']
            assert result_data['result']['artifacts'] == cli_result['artifacts']

    asyncio.run(check())
