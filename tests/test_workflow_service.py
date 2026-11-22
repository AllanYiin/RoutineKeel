"""Service contracts, read-only planning, and CLI parity."""
import json
import subprocess
import sys

import pytest
from flow_base.contracts import Step, Workflow
from flow_base.core.service import WorkflowService
from flow_base.core.types import PlanRequest, ServiceError, StartRequest
from flow_base.engine import Adapter, run_workflow
from flow_base.runtime import FlowError, atomic_json
from flow_base.storage.delivery import delivery_root
from flow_base.workspace import Workspace
from pydantic import ValidationError
from test_workspace import cli, completed_run


def test_readonly_discovery_does_not_persist_missing_identity(workspace):
    folder = workspace / 'ai_flow02'
    manifest = folder / 'flow.json'
    data = json.loads(manifest.read_text(encoding='utf-8'))
    data['id'] = None
    atomic_json(manifest, data)
    before = manifest.read_bytes()
    ws = Workspace(workspace)
    task = ws.task(folder.name, read_only=True)
    assert task.manifest.id.startswith('workflow_')
    assert task.flow_id.startswith('flow_')
    assert task.flow_id_status == 'provisional'
    assert manifest.read_bytes() == before
    assert not (workspace / 'share_states').exists()
    registered = Workspace(workspace).task(folder.name)
    assert registered.manifest.id == task.manifest.id
    assert registered.flow_id == task.flow_id


def test_service_plan_is_read_only_and_keeps_legacy_plan_shape(workspace):
    source = workspace / 'input.txt'
    source.write_text('read-only planning', encoding='utf-8')
    before = {path.relative_to(workspace).as_posix(): path.read_bytes()
              for path in workspace.rglob('*') if path.is_file()}
    service = WorkflowService(workspace)
    result = service.plan_workflow(PlanRequest(task='ai_flow02', source=source,
                                                output=workspace / 'result'))
    assert result.plan['ready_to_prepare'] is True
    assert result.plan['workflow'] == 'file-copy'
    assert result.execution_spec_hash and len(result.execution_spec_hash) == 64
    assert result.flow_id_status == 'provisional'
    after = {path.relative_to(workspace).as_posix(): path.read_bytes()
             for path in workspace.rglob('*') if path.is_file()}
    assert after == before
    assert not (workspace / 'share_states').exists()
    cli_plan = cli(workspace, 'plan', '--task', 'ai_flow02', '--input', source,
                   '--output', workspace / 'result')['result']
    assert not (workspace / 'share_states').exists()
    for key in ('workflow', 'ready_to_prepare', 'output', 'flow_id', 'output_layout'):
        assert result.plan[key] == cli_plan[key]


def test_service_start_idempotency_status_and_artifacts(workspace):
    source = workspace / 'input.txt'
    source.write_text('service result', encoding='utf-8')
    output = workspace / 'result'
    service = WorkflowService(workspace)
    planned = service.plan_workflow(PlanRequest(task='ai_flow02', source=source, output=output))
    request = StartRequest(task='ai_flow02', source=source, output=output,
                           idempotency_key='service-fixture',
                           expected_spec_hash=planned.execution_spec_hash)
    started = service.start_workflow(request)
    assert started.run_id.startswith('run_') and started.execution_spec_hash == planned.execution_spec_hash
    status = completed_run(service, started.run_id)
    assert status['execution_status'] == 'succeeded'
    assert (output / 'artifacts/result.bin').read_text(encoding='utf-8') == 'service result'
    assert service.start_workflow(request).run_id == started.run_id
    assert any(item['path'] == 'artifacts/result.bin' for item in service.list_run_artifacts(started.run_id))
    assert service.list_run_events(started.run_id)['events']
    assert service.diagnose_run(started.run_id)['execution_status'] == 'succeeded'
    source.write_text('different input', encoding='utf-8')
    with pytest.raises(FlowError) as mismatch:
        service.start_workflow(request)
    assert mismatch.value.code == 'RUN_SPEC_MISMATCH'
    with pytest.raises(FlowError) as reused_key:
        service.start_workflow(StartRequest(task='ai_flow02', source=source, output=output,
                                            idempotency_key='service-fixture'))
    assert reused_key.value.code == 'IDEMPOTENCY_CONFLICT'
    pointer = json.loads((delivery_root(output) / 'current.json').read_text(encoding='utf-8'))
    (delivery_root(output) / 'versions' / pointer['version'] / 'artifacts/result.bin').write_text('tampered', encoding='utf-8')
    with pytest.raises(FlowError) as invalid_artifact:
        service.list_run_artifacts(started.run_id)
    assert invalid_artifact.value.code == 'DELIVERY_INVALID'


def test_service_input_errors_do_not_create_project_state(workspace):
    service = WorkflowService(workspace)
    outside = workspace.parent / 'outside.txt'
    outside.write_text('outside', encoding='utf-8')
    with pytest.raises(FlowError) as forbidden:
        service.plan_workflow(PlanRequest(task='ai_flow02', source=outside))
    assert forbidden.value.code == 'PERMISSION_DENIED'
    with pytest.raises(FlowError) as still_forbidden:
        service.plan_workflow(PlanRequest(task='ai_flow02', source=outside,
                                          allow_external_paths=True))
    assert still_forbidden.value.code == 'PERMISSION_DENIED'
    authorized = WorkflowService(workspace, authorized_external_paths=[outside])
    assert authorized.plan_workflow(PlanRequest(task='ai_flow02', source=outside,
                                                allow_external_paths=True)).plan['ready_to_prepare']
    with pytest.raises(FlowError) as missing:
        service.plan_workflow(PlanRequest(task='missing'))
    assert missing.value.code == 'TASK_UNKNOWN'
    with pytest.raises(FlowError) as unknown_run:
        service.get_run_status('run_' + 'a'*32)
    assert unknown_run.value.code == 'RUN_UNKNOWN'
    assert not (workspace / 'share_states').exists()


def test_subtitle_plan_ignores_volatile_environment_readings(workspace):
    source = workspace / 'fixture.mp4'
    source.write_bytes(b'fixture only; planning does not decode media')
    service = WorkflowService(workspace)
    request = PlanRequest(task='ai_flow01', source=source, output=workspace / 'subtitles',
                          overrides={'profile': 'asr-cpu', 'options': {'device': 'cpu'}})
    before = {path.relative_to(workspace).as_posix(): path.stat().st_size
              for path in workspace.rglob('*') if path.is_file()}
    first = service.plan_workflow(request)
    second = service.plan_workflow(request)
    assert first.execution_spec_hash == second.execution_spec_hash
    assert first.execution_spec_hash is not None
    after = {path.relative_to(workspace).as_posix(): path.stat().st_size
             for path in workspace.rglob('*') if path.is_file()}
    assert after == before


def test_service_request_models_reject_mutation():
    request = PlanRequest(task='copy', overrides={'options': {'count': 1}, 'items': [1, 2]})
    with pytest.raises(ValidationError):
        request.task = 'changed'
    with pytest.raises(TypeError):
        request.overrides['options']['count'] = 2
    assert isinstance(request.overrides['items'], tuple)
    assert request.model_dump()['overrides']['options']['count'] == 1
    error = ServiceError(code='RUN_UNKNOWN', message='找不到指定 Run',
                         details={'run_id': 'run_' + 'a' * 32})
    assert error.contract_version == '1.0'
    with pytest.raises(TypeError):
        error.details['run_id'] = 'changed'


def test_cli_and_service_share_catalog_plan_and_sync_run(workspace):
    source = workspace / 'input.txt'
    source.write_text('same execution service', encoding='utf-8')
    output = workspace / 'result'
    service = WorkflowService(workspace)
    assert service.list_workflows() == cli(workspace, 'list')['result']
    request = PlanRequest(task='ai_flow02', source=source, output=output)
    assert service.plan_workflow(request).plan['workflow'] == cli(workspace, 'plan',
        '--task', 'ai_flow02', '--input', source, '--output', output)['result']['workflow']
    # A long-lived pytest process has loaded other versions of ai_flow02 in earlier
    # cases. Real Service clients start in their own process, as does the CLI.
    script = '''
import json, sys
from flow_base.core.service import WorkflowService
from flow_base.core.types import StartRequest
service = WorkflowService(sys.argv[1])
action = sys.argv[2]
if action == 'run':
    result = service.run_workflow_sync(StartRequest(task='ai_flow02', source=sys.argv[3], output=sys.argv[4]))
elif action == 'prepare':
    result = service.prepare_workflow('ai_flow02')
else:
    result = service.verify_workflow('ai_flow02')
print(json.dumps(result, ensure_ascii=False))
'''
    def direct_service(action):
        completed = subprocess.run([sys.executable, '-c', script, str(workspace), action,
                                    str(source), str(output)], capture_output=True, text=True, check=False)
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout.strip().splitlines()[-1])
    direct = direct_service('run')
    via_cli = cli(workspace, 'run', '--task', 'ai_flow02', '--input', source,
                  '--output', output)['result']
    assert (direct['flow_id'], direct['job_id'], direct['output'], direct['status']) == (
        via_cli['flow_id'], via_cli['job_id'], via_cli['output'], via_cli['status'])
    assert direct['run_id'] != via_cli['run_id']
    assert direct_service('prepare') == cli(workspace, 'prepare', '--task', 'ai_flow02')['result']
    assert direct_service('verify') == cli(workspace, 'verify', '--task', 'ai_flow02')['result']


def test_external_delivery_metadata_requires_explicit_core_authorization(workspace):
    source = workspace / 'input.txt'
    source.write_text('external result', encoding='utf-8')
    output = workspace.parent / (workspace.name + '-external-result')
    report = cli(workspace, 'run', '--task', 'ai_flow02', '--input', source,
                 '--output', output)['result']
    with pytest.raises(FlowError) as error:
        WorkflowService(workspace).list_run_artifacts(report['run_id'])
    assert error.value.code == 'PERMISSION_DENIED'
    allowed = WorkflowService(workspace, authorized_external_paths=[output])
    assert any(item['path'] == 'artifacts/result.bin' for item in
               allowed.list_run_artifacts(report['run_id']))


def test_service_open_project_is_read_only(workspace):
    service = WorkflowService(workspace)
    before = {path.relative_to(workspace).as_posix(): path.read_bytes()
              for path in workspace.rglob('*') if path.is_file()}
    info = service.open_project(workspace)
    assert info['exists'] is True and info['workflow_count'] == 2
    assert not (workspace / 'share_states').exists()
    after = {path.relative_to(workspace).as_posix(): path.read_bytes()
             for path in workspace.rglob('*') if path.is_file()}
    assert after == before


def test_service_export_is_scrubbed_and_path_authorized(workspace):
    failed = cli(workspace, 'run', '--task', 'ai_flow02', '--input',
                 'missing-private-marker.txt', ok=False)['error']
    run_id = failed['details']['run_id']
    target = workspace.parent / (workspace.name + '-export.json')
    service = WorkflowService(workspace)
    with pytest.raises(FlowError) as unauthorized:
        service.export_diagnostics(run_id, target)
    assert unauthorized.value.code == 'PERMISSION_DENIED'
    authorized = WorkflowService(workspace, authorized_external_paths=[target])
    with pytest.raises(FlowError) as raw:
        authorized.export_diagnostics(run_id, target, include_raw=True)
    assert raw.value.code == 'PERMISSION_DENIED'
    assert not target.exists()
    result = authorized.export_diagnostics(run_id, target)
    assert result['status'] == 'exported'
    assert 'missing-private-marker' not in target.read_text(encoding='utf-8')


def test_safe_retry_reuses_original_spec_without_reexecuting_side_effects(workspace, monkeypatch):
    monkeypatch.setattr('flow_base.engine.require_space', lambda *args: None)
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    adapter = Adapter()
    original = RuntimeError('transient pure-step failure')
    def fail_once(ctx, step, deps, target):
        raise original
    adapter.handlers = {'file.copy': fail_once}
    monkeypatch.setattr(task, 'load', lambda runtime: adapter)
    workflow = Workflow(id='file-copy', steps=[Step(id='copy', handler='file.copy')])
    source = workspace / 'input.txt'
    source.write_text('retry result', encoding='utf-8')
    output = workspace / 'result'
    with pytest.raises(RuntimeError) as caught:
        run_workflow(ws.runtime, task, workflow, source, output)
    assert caught.value is original
    failed_run = ws.runtime.history()[0]['run_id']
    service = WorkflowService(workspace)
    retried = service.retry_run(failed_run)
    state = completed_run(service, retried.run_id)
    assert state['execution_status'] == 'succeeded'
    assert (output / 'artifacts/result.bin').read_text(encoding='utf-8') == 'retry result'
    assert service.retry_run(failed_run).run_id == retried.run_id


def test_safe_retry_refuses_declared_side_effects(workspace, monkeypatch):
    monkeypatch.setattr('flow_base.engine.require_space', lambda *args: None)
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    adapter = Adapter()
    def fail(ctx, step, deps, target):
        raise RuntimeError('external side effect may have happened')
    adapter.handlers = {'fixture.effect': fail}
    monkeypatch.setattr(task, 'load', lambda runtime: adapter)
    workflow = Workflow(id='effect', steps=[Step(id='effect', handler='fixture.effect',
                                                side_effects='external')])
    source = workspace / 'input.txt'
    source.write_text('input', encoding='utf-8')
    with pytest.raises(RuntimeError):
        run_workflow(ws.runtime, task, workflow, source, workspace / 'result')
    failed_run = ws.runtime.history()[0]['run_id']
    before = len(ws.runtime.history())
    with pytest.raises(FlowError) as error:
        WorkflowService(workspace).retry_run(failed_run)
    assert error.value.code == 'RETRY_UNSAFE'
    assert len(ws.runtime.history()) == before


def test_service_cancel_is_request_until_process_stops(workspace):
    service = WorkflowService(workspace)
    task = service.workspace.task('ai_flow02')
    run_id = service.workspace.runtime.start_run(task.flow_id, 'fixture', workspace / 'result')
    from flow_base.core.lifecycle import RunStore
    store = RunStore(service.workspace.runtime)
    store.transition(run_id, 'created', 'queued')
    store.claim(run_id, token='fixture', pid=123, started_at=1.0)
    result = service.cancel_run(run_id)
    assert result['status'] == 'cancelling' and result['confirmed_stopped'] is False
    with pytest.raises(FlowError) as error:
        service.cancel_run(run_id)
    assert error.value.code == 'STATE_CONFLICT'
    store.transition(run_id, 'cancelling', 'cancelled')
    assert service.get_run_status(run_id)['execution_status'] == 'cancelled'
