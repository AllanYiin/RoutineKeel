"""Durable execution ID precedes Run ID and survives independent worker restart."""
import subprocess
import sys
import pytest

from flow_base.core.queue import QueueStore
from flow_base.core.service import WorkflowService
from flow_base.core.types import StartRequest, SubmitRequest
from flow_base.execution.queue_worker import run_queue_worker
from flow_base.runtime import FlowError
from test_workspace import workspace


def test_submit_idempotency_then_independent_worker_completes(workspace):
    source = workspace / 'input.txt'
    source.write_text('queued result', encoding='utf-8')
    service = WorkflowService(workspace)
    request = SubmitRequest(task='ai_flow02', source=source, output=workspace / 'result',
                            idempotency_key='queue-fixture')
    submitted = service.submit_workflow(request)
    assert submitted.status == 'queued' and submitted.execution_id.startswith('execution_')
    assert submitted.run_id is None
    assert service.submit_workflow(request).execution_id == submitted.execution_id
    assert service.get_workflow_status(submitted.execution_id)['status'] == 'queued'
    with pytest.raises(FlowError) as mismatch:
        service.submit_workflow(SubmitRequest(task='ai_flow02', source=source,
            output=workspace / 'different', idempotency_key='queue-fixture'))
    assert mismatch.value.code == 'IDEMPOTENCY_CONFLICT'
    processed = run_queue_worker(workspace, once=True, poll_interval=0.1)
    assert processed['processed'] == 1
    status = WorkflowService(workspace).get_workflow_status(submitted.execution_id)
    assert status['status'] == 'succeeded' and status['run_id'].startswith('run_')
    result = service.get_workflow_result(submitted.execution_id)
    assert result['result_available'] and result['run_id'] == status['run_id']
    assert any(item['path'] == 'artifacts/result.bin' for item in result['artifacts'])
    assert (workspace / 'result/artifacts/result.bin').read_text(encoding='utf-8') == 'queued result'


def test_queued_cancel_has_no_run_and_reclaim_dispatch_after_worker_crash(workspace):
    source = workspace / 'input.txt'
    source.write_text('second', encoding='utf-8')
    service = WorkflowService(workspace)
    first = service.submit_workflow(SubmitRequest(task='ai_flow02', source=source,
        output=workspace / 'cancelled')).execution_id
    cancelled = service.cancel_workflow(first)
    assert cancelled['status'] == 'cancelled' and cancelled['confirmed_stopped']
    assert cancelled['run_id'] is None
    second = service.submit_workflow(SubmitRequest(task='ai_flow02', source=source,
        output=workspace / 'reclaimed')).execution_id
    store = QueueStore(service.workspace.runtime)
    assert store.claim(second, 'crashed-worker')
    assert service.get_workflow_status(second)['status'] == 'dispatching'
    assert run_queue_worker(workspace, once=True, poll_interval=0.1)['processed'] == 1
    assert service.get_workflow_status(second)['status'] == 'succeeded'
    assert service.get_workflow_status(first)['status'] == 'cancelled'


def test_worker_reconnects_run_reserved_before_queue_link_is_saved(workspace):
    source = workspace / 'input.txt'
    source.write_text('reserved before crash', encoding='utf-8')
    output = workspace / 'result'
    service = WorkflowService(workspace)
    submitted = service.submit_workflow(SubmitRequest(task='ai_flow02', source=source,
                                                       output=output, offline=True))
    store = QueueStore(service.workspace.runtime)
    assert store.claim(submitted.execution_id, 'crashed-before-link')
    reserved = service.start_workflow(StartRequest(task='ai_flow02', source=source,
        output=output, offline=True, expected_spec_hash=submitted.execution_spec_hash,
        idempotency_key='execution:' + submitted.execution_id))
    assert store.get(submitted.execution_id)['run_id'] is None
    assert run_queue_worker(workspace, once=True, poll_interval=0.1)['processed'] == 1
    status = service.get_workflow_status(submitted.execution_id)
    assert status['status'] == 'succeeded' and status['run_id'] == reserved.run_id
    assert len([row for row in service.history() if row['run_id'] == reserved.run_id]) == 1


def test_cancel_during_dispatch_never_creates_run(workspace):
    source = workspace / 'input.txt'
    source.write_text('cancel during dispatch', encoding='utf-8')
    service = WorkflowService(workspace)
    submitted = service.submit_workflow(SubmitRequest(task='ai_flow02', source=source,
                                                       output=workspace / 'result'))
    store = QueueStore(service.workspace.runtime)
    assert store.claim(submitted.execution_id, 'crashed-worker')
    requested = service.cancel_workflow(submitted.execution_id)
    assert requested['status'] == 'cancel_requested' and not requested['confirmed_stopped']
    assert run_queue_worker(workspace, once=True, poll_interval=0.1)['processed'] == 1
    stopped = service.get_workflow_status(submitted.execution_id)
    assert stopped['status'] == 'cancelled' and stopped['run_id'] is None
    assert not service.history()


def test_two_independent_workers_never_duplicate_a_queued_run(workspace):
    source = workspace / 'input.txt'
    source.write_text('one writer', encoding='utf-8')
    service = WorkflowService(workspace)
    execution_id = service.submit_workflow(SubmitRequest(task='ai_flow02', source=source,
        output=workspace / 'result')).execution_id
    command = [sys.executable, '-m', 'flow_base', '--workspace', str(workspace),
               'queue-worker', '--idle-timeout', '2', '--poll-interval', '0.1']
    workers = [subprocess.Popen(command, stdin=subprocess.DEVNULL,
               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    outputs = [worker.communicate(timeout=20) for worker in workers]
    assert sorted(worker.returncode for worker in workers) == [0, 2], outputs
    status = service.get_workflow_status(execution_id)
    assert status['status'] == 'succeeded'
    assert sum(row['run_id'] == status['run_id'] for row in service.history()) == 1


def test_input_changed_while_queued_fails_without_creating_run(workspace):
    source = workspace / 'input.txt'
    source.write_text('planned version', encoding='utf-8')
    service = WorkflowService(workspace)
    execution_id = service.submit_workflow(SubmitRequest(task='ai_flow02', source=source,
        output=workspace / 'result')).execution_id
    source.write_text('changed after submit', encoding='utf-8')
    assert run_queue_worker(workspace, once=True, poll_interval=0.1)['processed'] == 1
    status = service.get_workflow_status(execution_id)
    assert status['status'] == 'failed' and status['run_id'] is None
    assert status['error_code'] == 'RUN_SPEC_MISMATCH'
    assert not service.history()
    assert not (workspace / 'result').exists()
