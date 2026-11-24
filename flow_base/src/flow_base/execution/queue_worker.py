"""User-started local queue worker; never a child of the MCP stdio process."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from ..core.queue import TERMINAL, QueueStore
from ..core.service import WorkflowService
from ..core.types import StartRequest
from ..runtime import FlowError


def run_queue_worker(workspace, *, once=False, poll_interval=0.5, idle_timeout=0):
    """Process one Run at a time. A second worker gets BUSY from the workspace lock."""
    if poll_interval <= 0 or idle_timeout < 0:
        raise FlowError('INVALID_CONFIGURATION', 'Worker 輪詢間隔與閒置時間不合法')
    service = WorkflowService(Path(workspace))
    runtime = service.workspace.runtime
    runtime.initialize()
    store = QueueStore(runtime)
    token = uuid.uuid4().hex
    completed = 0
    idle_since = time.monotonic()
    with runtime.lock('execution-queue-worker', timeout=0.1):
        while True:
            item = store.next_item()
            if item is None:
                if once or (idle_timeout and time.monotonic() - idle_since >= idle_timeout):
                    return {'status': 'stopped', 'processed': completed}
                time.sleep(poll_interval)
                continue
            idle_since = time.monotonic()
            execution_id = item['execution_id']
            if item['run_id']:
                run = service.get_run_status(item['run_id'])
                status = run['execution_status']
                if status in TERMINAL:
                    store.mark_terminal(execution_id, status)
                    completed += 1
                    if once:
                        return {'status': 'stopped', 'processed': completed}
                    continue
                if item['status'] == 'cancel_requested' and status not in {'cancelling', 'cancelled'}:
                    try:
                        service.cancel_run(item['run_id'])
                    except FlowError as exc:
                        if exc.code != 'STATE_CONFLICT':
                            raise
                store.heartbeat(execution_id, item['worker_token'])
                time.sleep(poll_interval)
                continue
            if item['status'] == 'cancel_requested':
                store.mark_terminal(execution_id, 'cancelled')
                completed += 1
                if once:
                    return {'status': 'stopped', 'processed': completed}
                continue
            claimed = store.claim(execution_id, token)
            if claimed is None:
                continue
            if store.get(execution_id)['status'] == 'cancel_requested':
                store.mark_terminal(execution_id, 'cancelled')
                completed += 1
                if once:
                    return {'status': 'stopped', 'processed': completed}
                continue
            try:
                started = service.start_workflow(StartRequest(
                    task=item['workflow_id'], source=Path(item['source']),
                    output=Path(item['output']), overrides=json.loads(item['overrides_json']),
                    offline=bool(item['offline']), idempotency_key='execution:' + execution_id,
                    expected_spec_hash=item['execution_spec_hash']))
            except FlowError as exc:
                if exc.code == 'BUSY':
                    store.requeue(execution_id, token)
                    time.sleep(poll_interval)
                    continue
                store.mark_terminal(execution_id, 'failed', error_code=exc.code)
                completed += 1
                if once:
                    return {'status': 'stopped', 'processed': completed}
                continue
            store.mark_running(execution_id, token, started.run_id)
            if store.get(execution_id)['status'] == 'cancel_requested':
                try:
                    service.cancel_run(started.run_id)
                except FlowError as exc:
                    if exc.code != 'STATE_CONFLICT':
                        raise
