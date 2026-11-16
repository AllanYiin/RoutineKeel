"""Cross-path Run/Action isolation under the single-writer workspace policy."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
import json
import os
import time

from flow_base.contracts import Step, Workflow
from flow_base.core.run_context import current
from flow_base.engine import Adapter, run_workflow
from flow_base.runtime import FlowError
from flow_base.workspace import Workspace
from test_workspace import workspace


def test_hundred_mixed_runs_and_actions_keep_identity_and_outputs(workspace, monkeypatch):
    monkeypatch.setattr('flow_base.engine.require_space', lambda *args: None)
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    task.manifest = task.manifest.model_copy(update={'actions': ['fixture-action']})
    adapter = Adapter()

    def write(ctx, step, dependencies, target):
        active = current(ws.runtime)
        assert active and active.run_id == ctx.run_id
        target.write_text(ctx.source.read_text(encoding='utf-8'), encoding='utf-8')
        return target

    def action(runtime, name, parameters):
        active = current(runtime)
        assert active and active.run_id == runtime.action_context['run_id']
        assert runtime.action_context['action'] == name
        return {'status': 'complete', 'marker': parameters['marker']}

    adapter.handlers = {'fixture.write': write}
    adapter.invoke_action = action
    monkeypatch.setattr(task, 'load', lambda runtime: adapter)
    workflow = Workflow(id='mixed-fixture', steps=[Step(id='write', handler='fixture.write')])
    sources = {}
    for index in range(0, 100, 10):
        source = workspace / f'input-{index:03d}.txt'
        source.write_text(f'marker-{index:03d}', encoding='utf-8')
        sources[index] = source
    deadline = time.monotonic() + 240

    def execute(index):
        marker = f'marker-{index:03d}'
        while time.monotonic() < deadline:
            try:
                if index % 10:
                    return index, task.invoke_action(ws.runtime, 'fixture-action', {'marker': marker})
                return index, run_workflow(ws.runtime, task, workflow, sources[index],
                                           workspace / f'out-{index:03d}')
            except FlowError as exc:
                if exc.code != 'BUSY':
                    raise
                time.sleep(0.02)
        raise AssertionError('workspace writer did not become available')

    with open(os.devnull, 'w') as quiet, redirect_stderr(quiet):
        with ThreadPoolExecutor(max_workers=4) as pool:
            completed = list(pool.map(execute, range(100)))
    assert len(completed) == 100
    run_ids = [report['run_id'] for _, report in completed]
    assert len(set(run_ids)) == 100
    for index, report in completed:
        run_id = report['run_id']
        logdir = ws.runtime.logs / task.flow_id / run_id
        saved = json.loads((logdir / 'run.json').read_text(encoding='utf-8'))
        assert saved['run_id'] == run_id
        events = ws.runtime.events(run_id, limit=100)['events']
        assert events and all(event['run_id'] == run_id for event in events)
        starts = [event for event in events if event['name'] == 'step.started']
        assert starts and all(event['step_attempt_id'] for event in starts)
        assert all(any(later['step_attempt_id'] == started['step_attempt_id'] and
                       later['name'] in {'step.finished', 'step.cache_hit'}
                       for later in events) for started in starts)
        if index % 10:
            assert report['result']['marker'] == f'marker-{index:03d}'
        else:
            assert (workspace / f'out-{index:03d}' / 'artifacts/result.bin').read_text(encoding='utf-8') == f'marker-{index:03d}'
