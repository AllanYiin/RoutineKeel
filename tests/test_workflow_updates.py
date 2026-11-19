"""Attachment updates preserve user data and survive process interruption."""
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from flow_base import package_updates as updates
from flow_base.core.queue import QueueStore
from flow_base.core.service import WorkflowService
from flow_base.core.types import StartRequest
from flow_base.runtime import FlowError, atomic_json
from test_workspace import cli, workspace  # noqa: F401 (pytest fixture)


def release(path, version='1.1.0', *, workflow_id='updatable', module='updatable_flow', extra=None):
    wheel = io.BytesIO()
    with zipfile.ZipFile(wheel, 'w') as archive:
        archive.writestr(module + '/__init__.py', '')
        archive.writestr(module + '/adapter.py', 'raise RuntimeError("ATTACHMENT_CODE_MUST_NOT_RUN")')
    import hashlib
    name = f'{module}-{version}-py3-none-any.whl'
    manifest = {'id': workflow_id, 'module': module, 'version': version, 'title': '更新測試',
                'description': '流程更新', 'package': name,
                'sha256': hashlib.sha256(wheel.getvalue()).hexdigest(), 'keywords': ['update']}
    files = {'flow.json': json.dumps(manifest, ensure_ascii=False).encode(), name: wheel.getvalue(),
             'README.md': version.encode(), 'run_app.sh': b'#!/bin/sh\nexit 0\n'}
    files.update(extra or {})
    with zipfile.ZipFile(path, 'w') as archive:
        for filename, content in files.items():
            archive.writestr('任意發行資料夾/' + filename, content)
    return files


@pytest.fixture
def installed(tmp_path):
    old_zip = tmp_path / 'old.zip'
    old = release(old_zip, '1.0.0')
    task = tmp_path / '使用者改過的流程名稱'
    task.mkdir()
    for name, body in old.items():
        (task / name).write_bytes(body)
    private = {'settings.json': b'{"private":"keep"}', 'outputs/job/artifacts/result.txt': b'USER_RESULT',
               'my-notes.md': b'PRIVATE_NOTES', '.env': b'PRIVATE_KEY'}
    for name, body in private.items():
        path = task / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    archive = tmp_path / '附件 新版.zip'
    release(archive)
    return WorkflowService(tmp_path), task, archive, private


def snapshot(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()}


def test_preview_update_and_rollback_keep_identity_private_data_and_history(installed):
    service, task, archive, private = installed
    before = snapshot(task)
    planned = service.preview_workflow_update(archive)
    assert planned['folder'] == task.name and planned['from_version'] == '1.0.0'
    assert planned['to_version'] == '1.1.0' and not planned['executes_package_code']
    assert not (service.workspace.root / 'share_states').exists()
    assert snapshot(task) == before
    runtime = service.workspace.runtime
    current = service.workspace.task(task.name)
    flow_id = current.flow_id
    run_id = runtime.start_run(flow_id, task / 'input', task / 'outputs/old')
    from flow_base.core.lifecycle import RunStore
    RunStore(runtime).finish_failure(run_id)
    log = runtime.logs / flow_id / run_id / 'private.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(b'HISTORY_LOG')
    cache = runtime.home / 'environments/existing-model.bin'
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b'MODEL_CACHE')
    result = service.update_workflow(archive, plan_hash=planned['plan_hash'])
    assert result['status'] == 'updated' and Path(result['backup_path']).is_dir()
    assert not result['restart_required']
    assert service.workspace.task(task.name).flow_id == flow_id
    assert service.workspace.task(task.name).manifest.version == '1.1.0'
    assert any(item['run_id'] == run_id for item in service.history())
    assert log.read_bytes() == b'HISTORY_LOG' and cache.read_bytes() == b'MODEL_CACHE'
    for name, body in private.items():
        assert (task / name).read_bytes() == body
    again = service.preview_workflow_update(archive)
    assert again['status'] == 'already_current'
    assert service.update_workflow(archive, plan_hash=again['plan_hash'])['status'] == 'already_current'
    assert service.rollback_workflow_update(result['update_id'])['status'] == 'rolled_back'
    assert snapshot(task) == before
    assert service.recover_workflow_update()['status'] == 'no_pending_update'


@pytest.mark.parametrize('kind', ['identity', 'module', 'downgrade', 'same_version', 'private', 'outputs', 'nested', 'base', 'wheel_hash'])
def test_reject_invalid_or_wrong_attachment_without_touching_installed_files(installed, kind):
    service, task, archive, _ = installed
    before = snapshot(task)
    if kind == 'identity':
        release(archive, workflow_id='different')
    elif kind == 'module':
        release(archive, module='different')
    elif kind == 'downgrade':
        release(archive, '0.9.0')
    elif kind == 'same_version':
        release(archive, '1.0.0', extra={'README.md': b'changed under same version'})
    elif kind in {'private', 'outputs', 'nested'}:
        release(archive, extra={dict(private='settings.json', outputs='outputs/result.txt', nested='../outside')[kind]: b'BAD'})
    elif kind == 'base':
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('flow_base/start.sh', 'exit 0')
    else:
        release(archive, extra={'updatable_flow-1.1.0-py3-none-any.whl': b'TAMPERED'})
    with pytest.raises(FlowError):
        service.preview_workflow_update(archive, task=task.name)
    assert snapshot(task) == before
    assert not (service.workspace.root / 'share_states').exists()


@pytest.mark.parametrize('member', ['../outside', '/outside', 'C:/outside', 'CON/file', 'new/file.', 'new/x:y', 'new/a\\b', 'new/./flow.json'])
def test_zip_path_validation(installed, member):
    service, task, archive, _ = installed
    before = snapshot(task)
    with zipfile.ZipFile(archive, 'a') as z:
        z.writestr(member, b'BAD')
    with pytest.raises(FlowError, match='ZIP|壓縮包'):
        service.preview_workflow_update(archive)
    assert snapshot(task) == before


def test_reject_duplicate_symlink_and_oversized_members(installed, monkeypatch):
    service, _, archive, _ = installed
    with zipfile.ZipFile(archive, 'a') as z:
        info = zipfile.ZipInfo('任意發行資料夾/link')
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        z.writestr(info, 'README.md')
    with pytest.raises(FlowError):
        service.preview_workflow_update(archive)
    release(archive)
    with zipfile.ZipFile(archive, 'a') as z:
        z.writestr('任意發行資料夾/readme.md', b'duplicate case')
    with pytest.raises(FlowError):
        service.preview_workflow_update(archive)
    release(archive)
    monkeypatch.setattr(updates, 'MAX_ZIP', 10)
    with pytest.raises(FlowError):
        service.preview_workflow_update(archive)


def test_stale_preview_cannot_overwrite_new_attachment_or_local_edit(installed):
    service, task, archive, _ = installed
    plan = service.preview_workflow_update(archive)
    (task / 'README.md').write_bytes(b'LOCAL_CHANGE')
    with pytest.raises(FlowError, match='重新預覽'):
        service.update_workflow(archive, plan_hash=plan['plan_hash'])
    plan = service.preview_workflow_update(archive)
    release(archive, '1.2.0')
    with pytest.raises(FlowError, match='重新預覽'):
        service.update_workflow(archive, plan_hash=plan['plan_hash'])
    assert (task / 'README.md').read_bytes() == b'LOCAL_CHANGE'


@pytest.mark.parametrize('busy', ['run', 'queue', 'lock'])
def test_active_work_and_concurrent_operations_block_update(installed, busy):
    service, task, archive, _ = installed
    plan = service.preview_workflow_update(archive)
    runtime = service.workspace.runtime
    runtime.initialize()
    if busy == 'run':
        flow = service.workspace.task(task.name)
        runtime.start_run(flow.flow_id, 'input', 'output')
    elif busy == 'queue':
        flow = service.workspace.task(task.name)
        QueueStore(runtime).submit(workflow_id=flow.manifest.id, flow_id=flow.flow_id,
            source='input', output='output', overrides={}, offline=True, spec_hash='a' * 64)
    if busy == 'lock':
        with updates.package_lock(service.workspace):
            with pytest.raises(FlowError) as exc:
                service.update_workflow(archive, plan_hash=plan['plan_hash'])
    else:
        with pytest.raises(FlowError) as exc:
            service.update_workflow(archive, plan_hash=plan['plan_hash'])
    assert exc.value.code == 'BUSY'
    assert json.loads((task / 'flow.json').read_text())['version'] == '1.0.0'


def test_write_failure_restores_all_package_files(installed, monkeypatch):
    service, task, archive, _ = installed
    before = snapshot(task)
    plan = service.preview_workflow_update(archive)
    original = updates._write_file
    failed = False

    def fail_once(path, body, mode):
        nonlocal failed
        original(path, body, mode)
        if path.parent == task and path.name == 'README.md' and not failed:
            failed = True
            raise OSError('injected disk failure')
    monkeypatch.setattr(updates, '_write_file', fail_once)
    with pytest.raises(OSError):
        service.update_workflow(archive, plan_hash=plan['plan_hash'])
    assert failed and snapshot(task) == before
    assert service.recover_workflow_update()['status'] == 'no_pending_update'


@pytest.mark.parametrize('crash_at', ['README.md', 'flow.json', 'receipt'])
def test_killed_updater_recovers_without_original_zip(installed, crash_at):
    service, task, archive, _ = installed
    before = snapshot(task)
    plan = service.preview_workflow_update(archive)
    script = '''
import os, sys
from pathlib import Path
from flow_base import package_updates as u
from flow_base.core.service import WorkflowService
root, archive, folder, plan_hash, crash_at = sys.argv[1:]
original = u._write_file
def write(path, body, mode):
    original(path, body, mode)
    if path.parent == Path(folder) and path.name == crash_at:
        os._exit(77)
u._write_file = write
original_json = u.atomic_json
def write_json(path, value):
    original_json(path, value)
    if crash_at == 'receipt' and path.name == 'record.json' and value.get('status') == 'applied':
        os._exit(77)
u.atomic_json = write_json
WorkflowService(root).update_workflow(archive, plan_hash=plan_hash)
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'routinekeel/flow_base/src'))
    result = subprocess.run([sys.executable, '-c', script, str(service.workspace.root), str(archive),
                             str(task), plan['plan_hash'], crash_at], env=env, capture_output=True, timeout=20)
    assert result.returncode == 77, result.stderr
    archive.unlink()
    with pytest.raises(FlowError) as blocked:
        service.run_workflow_sync(StartRequest(task='updatable', source='input'))
    assert blocked.value.code == 'WORKFLOW_UPDATE_PENDING'
    assert service.recover_workflow_update()['status'] == 'rolled_back'
    assert snapshot(task) == before


def test_rollback_protects_later_changes_and_corrupt_backup(installed):
    service, task, archive, _ = installed
    plan = service.preview_workflow_update(archive)
    result = service.update_workflow(archive, plan_hash=plan['plan_hash'])
    (task / 'README.md').write_bytes(b'NEW_LOCAL_EDIT')
    with pytest.raises(FlowError, match='後續修改'):
        service.rollback_workflow_update(result['update_id'])
    (task / 'README.md').write_bytes(b'1.1.0')
    (Path(result['backup_path']) / 'before/README.md').write_bytes(b'BAD_BACKUP')
    with pytest.raises(FlowError, match='備份損壞'):
        service.rollback_workflow_update(result['update_id'])
    assert json.loads((task / 'flow.json').read_text())['version'] == '1.1.0'


def test_service_external_attachment_requires_explicit_path_authorization(installed, tmp_path):
    service, _, archive, _ = installed
    external_service = WorkflowService(tmp_path / 'different-workspace')
    with pytest.raises(FlowError) as exc:
        external_service.preview_workflow_update(archive)
    assert exc.value.code == 'PERMISSION_DENIED'
    authorized = WorkflowService(service.workspace.root, authorized_external_paths=[archive])
    assert authorized.preview_workflow_update(archive)['status'] == 'ready'


def test_legacy_missing_id_keeps_installed_identity(installed):
    service, task, archive, _ = installed
    data = json.loads((task / 'flow.json').read_text())
    data['id'] = None
    atomic_json(task / 'flow.json', data)
    old_id = service.workspace.task(task.name, read_only=True).manifest.id
    release(archive, workflow_id=None)
    plan = service.preview_workflow_update(archive)
    service.update_workflow(archive, plan_hash=plan['plan_hash'])
    assert service.workspace.task(task.name).manifest.id == old_id


def test_cli_attachment_preview_apply_recover_and_rollback(installed):
    service, task, archive, _ = installed
    root = service.workspace.root
    before = snapshot(task)
    plan = cli(root, 'update-workflow', '--archive', archive)['result']
    missing = cli(root, 'update-workflow', '--archive', archive, '--execute', ok=False)
    assert missing['error']['code'] == 'INVALID_CONFIGURATION'
    result = cli(root, 'update-workflow', '--archive', archive, '--execute',
                 '--plan-hash', plan['plan_hash'])['result']
    assert result['status'] == 'updated'
    assert cli(root, 'recover-workflow-update')['result']['status'] == 'no_pending_update'
    assert cli(root, 'rollback-workflow-update', result['update_id'])['result']['status'] == 'rolled_back'
    assert snapshot(task) == before


def test_real_flow_runs_before_after_update_and_after_rollback(workspace):  # noqa: F811
    task = workspace / 'ai_flow02'
    old = json.loads((task / 'flow.json').read_text(encoding='utf-8'))
    parts = list(map(int, old['version'].split('.')))
    version = '.'.join(map(str, [parts[0], parts[1], parts[2] + 1]))
    wheel_body = io.BytesIO()
    with zipfile.ZipFile(task / old['package']) as source, zipfile.ZipFile(wheel_body, 'w') as target:
        for member in source.infolist():
            name = member.filename.replace('-' + old['version'] + '.dist-info/', '-' + version + '.dist-info/')
            body = source.read(member)
            if name.endswith('.dist-info/METADATA'):
                body = body.replace(('Version: ' + old['version']).encode(), ('Version: ' + version).encode())
            target.writestr(name, body)
    import hashlib
    new = {**old, 'version': version, 'package': old['package'].replace(old['version'], version),
           'sha256': hashlib.sha256(wheel_body.getvalue()).hexdigest()}
    archive = workspace / '實際流程 新版.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('新資料夾/flow.json', json.dumps(new, ensure_ascii=False))
        z.writestr('新資料夾/' + new['package'], wheel_body.getvalue())
        z.writestr('新資料夾/README.md', '新版')
    (task / 'settings.json').write_text('{}', encoding='utf-8')
    source = workspace / 'input.txt'
    source.write_text('keep old and new results', encoding='utf-8')
    before = cli(workspace, 'run', '--task', 'ai_flow02', '--input', source)['result']
    plan = cli(workspace, 'update-workflow', '--archive', archive)['result']
    updated = cli(workspace, 'update-workflow', '--archive', archive,
                  '--execute', '--plan-hash', plan['plan_hash'])['result']
    after = cli(workspace, 'run', '--task', 'ai_flow02', '--input', source,
                '--output', task / 'outputs/new-version')['result']
    assert before['flow_id'] == after['flow_id'] and before['run_id'] != after['run_id']
    assert (Path(before['output']) / 'artifacts/result.bin').read_bytes() == source.read_bytes()
    assert (Path(after['output']) / 'artifacts/result.bin').read_bytes() == source.read_bytes()
    cli(workspace, 'rollback-workflow-update', updated['update_id'])
    restored = cli(workspace, 'run', '--task', 'ai_flow02', '--input', source,
                   '--output', task / 'outputs/rolled-back')['result']
    assert restored['flow_id'] == before['flow_id']
    assert json.loads((task / 'flow.json').read_text(encoding='utf-8')) == old
    assert (task / 'settings.json').read_text() == '{}'
    history = cli(workspace, 'history')['result']
    assert {r['run_id'] for r in history} >= {before['run_id'], after['run_id'], restored['run_id']}


def test_stdio_attachment_update_and_rollback(installed):
    pytest.importorskip('mcp')
    import asyncio
    from test_mcp_stdio import client_for, call
    service, task, archive, _ = installed

    async def exercise():
        async with client_for(service.workspace.root) as client:
            rejected = await call(client, 'preview_workflow_update', archive=str(archive.parent.parent / 'private.zip'))
            assert rejected['error']['code'] == 'PERMISSION_DENIED'
            preview = await call(client, 'preview_workflow_update', archive=str(archive))
            assert preview['ok'], preview
            result = await call(client, 'update_workflow', archive=str(archive),
                                plan_hash=preview['result']['plan_hash'])
            assert result['ok'] and result['result']['status'] == 'updated', result
            rollback = await call(client, 'rollback_workflow_update', update_id=result['result']['update_id'])
            assert rollback['ok'] and rollback['result']['status'] == 'rolled_back', rollback
            recovery = await call(client, 'recover_workflow_update')
            assert recovery['result']['status'] == 'no_pending_update'
    asyncio.run(exercise())
    assert json.loads((task / 'flow.json').read_text())['version'] == '1.0.0'


def test_recovery_itself_can_be_interrupted_and_retried(installed, monkeypatch):
    service, task, archive, _ = installed
    before = snapshot(task)
    plan = service.preview_workflow_update(archive)
    applied = service.update_workflow(archive, plan_hash=plan['plan_hash'])
    original = updates._write_file

    def fail_restore(path, body, mode):
        original(path, body, mode)
        if path.parent == task and path.name == 'README.md':
            raise OSError('restore interruption')
    monkeypatch.setattr(updates, '_write_file', fail_restore)
    with pytest.raises(OSError):
        service.rollback_workflow_update(applied['update_id'])
    assert (updates.update_root(service.workspace) / 'pending.json').exists()
    monkeypatch.setattr(updates, '_write_file', original)
    assert service.recover_workflow_update()['status'] == 'rolled_back'
    assert snapshot(task) == before


def test_update_refuses_linked_state(installed, tmp_path):
    service, _, archive, _ = installed
    target = tmp_path / 'external-state'
    target.mkdir()
    link = tmp_path / 'share_states'
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('OS does not permit test symlinks')
    with pytest.raises(FlowError) as exc:
        service.preview_workflow_update(archive)
    assert exc.value.code == 'PERMISSION_DENIED'
    assert snapshot(target) == {}
