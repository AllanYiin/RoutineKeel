"""A failed second publish never displaces the Service-visible prior delivery."""
import pytest

from flow_base.contracts import Step, Workflow
from flow_base.core.service import WorkflowService
from flow_base.engine import Adapter, run_workflow
from flow_base.storage.delivery import delivery_root
from flow_base.workspace import Workspace
from test_workspace import workspace


def test_service_keeps_old_artifacts_when_second_publish_fails(workspace, monkeypatch):
    monkeypatch.setattr('flow_base.engine.require_space', lambda *args: None)
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    adapter = Adapter()

    def write(ctx, step, dependencies, target):
        target.write_text(ctx.source.read_text(encoding='utf-8'), encoding='utf-8')
        return target

    adapter.handlers = {'fixture.write': write}
    monkeypatch.setattr(task, 'load', lambda runtime: adapter)
    workflow = Workflow(id='publish-fixture', steps=[Step(id='write', handler='fixture.write',
                                                          cacheable=False)])
    source = workspace / 'input.txt'
    source.write_text('first delivery', encoding='utf-8')
    output = workspace / 'result'
    first = run_workflow(ws.runtime, task, workflow, source, output)
    pointer = (delivery_root(output) / 'current.json').read_bytes()
    service = WorkflowService(workspace)
    first_artifacts = service.list_run_artifacts(first['run_id'])
    assert any(item['path'] == 'artifacts/result.bin' for item in first_artifacts)

    def fail_publish(*_args, **_kwargs):
        raise RuntimeError('injected publish failure')
    monkeypatch.setattr('flow_base.storage.delivery.promote_staging', fail_publish)
    with pytest.raises(RuntimeError, match='injected publish failure'):
        run_workflow(ws.runtime, task, workflow, source, output)
    failed_run = ws.runtime.history()[0]['run_id']
    assert failed_run != first['run_id']
    assert (delivery_root(output) / 'current.json').read_bytes() == pointer
    assert (output / 'artifacts/result.bin').read_text(encoding='utf-8') == 'first delivery'
    assert service.list_run_artifacts(first['run_id']) == first_artifacts
    assert service.list_run_artifacts(failed_run) == []
    diagnostic = service.diagnose_run(failed_run)
    assert diagnostic['execution_status'] == 'failed' and diagnostic['error_id']
