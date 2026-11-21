"""Delivery staging protects the last published result before promotion."""
import json
import os
import subprocess
import sys
import threading
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from flow_base.runtime import FlowError, read_json
from flow_base.storage.delivery import (prepare_staging, staging_path,
    promote_staging, delivery_root, verify_version, recover_transactions,
    delivery_gc_preview, delivery_gc_apply, rollback_delivery)
from flow_base.core.lifecycle import RunStore
from flow_base.runtime import Runtime
from flow_base.engine import Adapter, run_workflow
from flow_base.contracts import Workflow, Step
from flow_base.workspace import Workspace
from test_workspace import workspace


def test_staging_clones_existing_result_without_mutation(tmp_path):
    output = tmp_path / 'job'
    (output / 'artifacts').mkdir(parents=True)
    (output / 'artifacts' / 'result.txt').write_text('old', encoding='utf-8')
    (output / 'artifacts' / 'edits.json').write_text('{"human":true}', encoding='utf-8')
    (output / 'report.json').write_text('{"status":"complete"}', encoding='utf-8')
    stage = prepare_staging(output, 'run_' + 'a'*32)
    assert stage != output and stage.anchor == output.anchor
    assert (stage / 'artifacts' / 'edits.json').read_text(encoding='utf-8') == '{"human":true}'
    (stage / 'artifacts' / 'result.txt').write_text('new', encoding='utf-8')
    assert (output / 'artifacts' / 'result.txt').read_text(encoding='utf-8') == 'old'
    assert json.loads((output / 'report.json').read_text())['status'] == 'complete'
    with pytest.raises(FlowError) as error:
        prepare_staging(output, 'run_' + 'a'*32)
    assert error.value.code == 'DELIVERY_STAGE_EXISTS'


def test_first_run_staging_leaves_public_output_absent(tmp_path):
    output = tmp_path / 'job'
    stage = prepare_staging(output, 'run_' + 'b'*32)
    assert stage.is_dir() and not output.exists()


def test_staging_rejects_symlink_in_previous_result(tmp_path):
    output = tmp_path / 'job'
    output.mkdir()
    external = tmp_path / 'external.txt'
    external.write_text('private', encoding='utf-8')
    try:
        (output / 'linked.txt').symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip('symlink unavailable')
    with pytest.raises(FlowError) as error:
        prepare_staging(output, 'run_' + 'c'*32)
    assert error.value.code == 'OUTPUT_LINK'


def _publishing_run(store, job='job'):
    run_id = store.create(job_id=job, revision_id='rev', spec_hash='spec')
    store.transition(run_id, 'created', 'queued')
    store.claim(run_id, token='token', pid=123, started_at=1.0)
    store.transition(run_id, 'preparing', 'running')
    store.transition(run_id, 'running', 'publishing')
    return run_id


def test_delivery_remains_staged_until_explicit_publish(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    store = RunStore(runtime)
    run_id = _publishing_run(store)
    manifest = tmp_path / 'version' / 'delivery-manifest.json'
    manifest.parent.mkdir()
    manifest.write_text('{"files":[]}', encoding='utf-8')
    delivery = store.stage_delivery(run_id, manifest)
    assert store.stage_delivery(run_id, manifest) == delivery
    with runtime.connect() as db:
        assert db.execute('SELECT status FROM deliveries WHERE delivery_id=?', (delivery,)).fetchone()[0] == 'staged'
    store.publish_delivery(delivery)
    store.transition(run_id, 'publishing', 'succeeded')
    with runtime.connect() as db:
        assert db.execute('SELECT status FROM deliveries WHERE delivery_id=?', (delivery,)).fetchone()[0] == 'published'


def test_second_delivery_supersedes_but_keeps_previous_version(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    store = RunStore(runtime)
    first_run = _publishing_run(store)
    first_manifest = tmp_path / 'one.json'
    first_manifest.write_text('first', encoding='utf-8')
    first = store.stage_delivery(first_run, first_manifest)
    store.publish_delivery(first)
    store.transition(first_run, 'publishing', 'succeeded')
    second_run = _publishing_run(store)
    second_manifest = tmp_path / 'two.json'
    second_manifest.write_text('second', encoding='utf-8')
    second = store.stage_delivery(second_run, second_manifest)
    store.publish_delivery(second)
    with runtime.connect() as db:
        prior = db.execute('SELECT status,superseded_by FROM deliveries WHERE delivery_id=?', (first,)).fetchone()
        current = db.execute('SELECT status FROM deliveries WHERE delivery_id=?', (second,)).fetchone()
    assert tuple(prior) == ('superseded', second)
    assert current[0] == 'published'
    assert first_manifest.read_text(encoding='utf-8') == 'first'


def _stage_result(output, run_id, content):
    stage = prepare_staging(output, run_id)
    (stage / 'artifacts').mkdir(exist_ok=True)
    (stage / 'artifacts' / 'result.txt').write_text(content, encoding='utf-8')
    (stage / 'artifacts' / 'extra.txt').write_text(content + '-extra', encoding='utf-8')
    (stage / 'docs').mkdir(exist_ok=True)
    (stage / 'docs' / 'run-summary.md').write_text('# ' + content, encoding='utf-8')
    (stage / 'deliverables.json').write_text(json.dumps({'run_id': run_id, 'files': []}), encoding='utf-8')
    (stage / 'report.json').write_text(json.dumps({'run_id': run_id, 'status': 'complete'}), encoding='utf-8')
    return stage


def test_promote_keeps_immutable_versions_and_pointer(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    store = RunStore(runtime)
    output = tmp_path / 'job'
    first_run = _publishing_run(store)
    first = promote_staging(output, _stage_result(output, first_run, 'one'), first_run, store)
    assert (output / 'artifacts/result.txt').read_text() == 'one'
    assert verify_version(first['version'])
    second_run = _publishing_run(store)
    second = promote_staging(output, _stage_result(output, second_run, 'two'), second_run, store)
    assert (output / 'artifacts/result.txt').read_text() == 'two'
    assert (Path(first['version']) / 'artifacts/result.txt').read_text() == 'one'
    assert read_json(delivery_root(output) / 'current.json')['delivery_id'] == second['delivery_id']


@pytest.mark.parametrize('fault', [
    'staging_verified', 'version_renamed', 'db_staged', 'previous_version_saved',
    'transaction_recorded',
    'before_mirror:artifacts/extra.txt', 'after_old_move:artifacts/extra.txt',
    'after_new_copy:artifacts/extra.txt',
    'before_mirror:artifacts/result.txt', 'after_old_move:artifacts/result.txt',
    'after_new_copy:artifacts/result.txt',
    'before_mirror:deliverables.json', 'after_old_move:deliverables.json',
    'after_new_copy:deliverables.json',
    'before_mirror:docs/run-summary.md', 'after_old_move:docs/run-summary.md',
    'after_new_copy:docs/run-summary.md',
    'before_mirror:report.json', 'after_new_copy:report.json', 'after_pointer',
])
def test_failure_during_promotion_restores_previous_result(tmp_path, fault):
    runtime = Runtime(tmp_path / 'share_states')
    store = RunStore(runtime)
    output = tmp_path / 'job'
    first_run = _publishing_run(store)
    first = promote_staging(output, _stage_result(output, first_run, 'one'), first_run, store)
    previous_pointer = read_json(delivery_root(output) / 'current.json')
    previous_report = (output / 'report.json').read_bytes()
    second_run = _publishing_run(store)
    def inject(name):
        if name == fault:
            raise RuntimeError('injected promotion failure')
    with pytest.raises(RuntimeError, match='injected'):
        promote_staging(output, _stage_result(output, second_run, 'two'), second_run,
                        store, checkpoint=inject)
    assert (output / 'artifacts/result.txt').read_text() == 'one'
    assert (output / 'report.json').read_bytes() == previous_report
    assert read_json(delivery_root(output) / 'current.json') == previous_pointer
    with runtime.connect() as db:
        assert db.execute('SELECT status FROM deliveries WHERE run_id=?', (first_run,)).fetchone()[0] == 'published'
        row = db.execute('SELECT status FROM deliveries WHERE run_id=?', (second_run,)).fetchone()
    if fault in {'staging_verified', 'version_renamed'}:
        assert row is None
    else:
        assert row[0] == 'aborted'


@pytest.mark.parametrize('crash_point', ['after_new_copy:report.json', 'after_pointer'])
def test_crash_mid_mirror_is_recovered_from_journal(tmp_path, crash_point):
    runtime = Runtime(tmp_path / 'share_states')
    store = RunStore(runtime)
    output = tmp_path / 'job'
    first_run = _publishing_run(store)
    promote_staging(output, _stage_result(output, first_run, 'one'), first_run, store)
    previous_pointer = read_json(delivery_root(output) / 'current.json')
    second_run = _publishing_run(store)
    with runtime.connect() as db:
        db.execute("INSERT INTO flows VALUES('flow_fixture','fixture','0.1.0','hash','Fixture',1,1)")
        db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)',
                   (second_run, 'flow_fixture', 'job', 'running', 'fixture', str(output), 1, None, None))
    stage = _stage_result(output, second_run, 'two')
    code = (
        'import os,sys; from pathlib import Path; from flow_base.runtime import Runtime; '
        'from flow_base.core.lifecycle import RunStore; '
        'from flow_base.storage.delivery import promote_staging; '
        'r=Runtime(Path(sys.argv[1])); '
        'promote_staging(Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4],RunStore(r), '
        'checkpoint=lambda name:os._exit(17) if name==sys.argv[5] else None)'
    )
    env = os.environ.copy()
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'flow_base/src')
    crashed = subprocess.run([sys.executable, '-c', code, str(runtime.home), str(output), str(stage), second_run, crash_point],
                             env=env, capture_output=True, text=True, timeout=15)
    assert crashed.returncode == 17, crashed.stderr
    assert recover_transactions(output, store) == []  # active lease is not presumed dead
    with runtime.connect() as db:
        db.execute("UPDATE run_executions SET lease_expires_at='0' WHERE run_id=?", (second_run,))
    Runtime(runtime.home).initialize()  # project reopen reconciles the lost supervisor, then the journal
    assert (output / 'artifacts/result.txt').read_text() == 'one'
    assert read_json(delivery_root(output) / 'current.json') == previous_pointer
    with runtime.connect() as db:
        assert db.execute('SELECT status FROM deliveries WHERE run_id=?', (second_run,)).fetchone()[0] == 'aborted'
        assert db.execute('SELECT execution_status FROM run_executions WHERE run_id=?', (second_run,)).fetchone()[0] == 'interrupted'


def test_preexisting_published_manifest_is_relocated_to_legacy_snapshot(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    store = RunStore(runtime)
    output = tmp_path / 'job'
    (output / 'artifacts').mkdir(parents=True)
    (output / 'artifacts/result.txt').write_text('legacy', encoding='utf-8')
    first_run = _publishing_run(store)
    (output / 'report.json').write_text(json.dumps({'run_id': first_run, 'status': 'complete'}), encoding='utf-8')
    first_delivery = store.record_delivery(first_run, output / 'report.json')
    store.transition(first_run, 'publishing', 'succeeded')
    second_run = _publishing_run(store)
    promote_staging(output, _stage_result(output, second_run, 'new'), second_run, store)
    with runtime.connect() as db:
        row = db.execute('SELECT a.manifest_json,a.manifest_sha256 FROM deliveries d JOIN artifact_sets a ON a.artifact_set_id=d.artifact_set_id WHERE d.delivery_id=?',
                         (first_delivery,)).fetchone()
    relocated = Path(json.loads(row['manifest_json'])['path'])
    assert relocated != output / 'report.json'
    assert relocated.is_file()
    assert json.loads(relocated.read_text())['run_id'] == first_run
    assert (relocated.parent / 'artifacts/result.txt').read_text() == 'legacy'


@pytest.mark.parametrize('failure_site', ['handler', 'publish'])
def test_failed_rerun_does_not_change_public_result(workspace, monkeypatch, failure_site):
    monkeypatch.setattr('flow_base.engine.require_space', lambda *args: None)
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    adapter = Adapter()
    def handler(ctx, step, dependencies, target):
        target.write_text('original', encoding='utf-8')
        return target
    adapter.handlers = {'fixture.write': handler}
    monkeypatch.setattr(task, 'load', lambda runtime: adapter)
    workflow = Workflow(id='fixture', steps=[Step(id='write', handler='fixture.write', cacheable=False)])
    source = workspace / 'input.txt'
    source.write_text('input', encoding='utf-8')
    output = workspace / 'result'
    first = run_workflow(ws.runtime, task, workflow, source, output)
    before = {name: path.read_bytes() for name, path in
              ((path.relative_to(output).as_posix(), path) for path in output.rglob('*') if path.is_file())}
    pointer = read_json(delivery_root(output) / 'current.json')
    def fail_handler(*args):
        raise RuntimeError('injected handler error')
    def fail_publish(terminal, destination, workflow):
        (destination / 'partial.txt').write_text('partial', encoding='utf-8')
        raise RuntimeError('injected publish error')
    if failure_site == 'handler':
        adapter.handlers['fixture.write'] = fail_handler
    else:
        monkeypatch.setattr(adapter, 'publish', fail_publish)
    with pytest.raises(RuntimeError, match='injected'):
        run_workflow(ws.runtime, task, workflow, source, output)
    after = {name: path.read_bytes() for name, path in
             ((path.relative_to(output).as_posix(), path) for path in output.rglob('*') if path.is_file())}
    assert after == before
    assert read_json(delivery_root(output) / 'current.json') == pointer
    assert read_json(output / 'report.json')['run_id'] == first['run_id']


def test_delivery_gc_only_lists_unreferenced_abandoned_version(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    store = RunStore(runtime)
    output = tmp_path / 'job'
    first_run = _publishing_run(store)
    first = promote_staging(output, _stage_result(output, first_run, 'one'), first_run, store)
    second_run = _publishing_run(store)
    def fault(name):
        if name == 'version_renamed':
            raise RuntimeError('abandoned before DB staging')
    with pytest.raises(RuntimeError):
        promote_staging(output, _stage_result(output, second_run, 'two'), second_run, store,
                        checkpoint=fault)
    store.finish_failure(second_run)
    preview = delivery_gc_preview(runtime, output)
    assert {Path(item['path']).name for item in preview['candidates']} == {second_run}
    with pytest.raises(FlowError):
        delivery_gc_apply(runtime, output)
    removed = delivery_gc_apply(runtime, output, confirm=True)
    assert removed['bytes'] > 0
    assert not (delivery_root(output) / 'versions' / second_run).exists()
    assert Path(first['version']).is_dir()
    assert (output / 'artifacts/result.txt').read_text() == 'one'


def test_database_publish_failure_rolls_back_pointer_and_mirror(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path / 'share_states')
    store = RunStore(runtime)
    output = tmp_path / 'job'
    first_run = _publishing_run(store)
    promote_staging(output, _stage_result(output, first_run, 'one'), first_run, store)
    previous = read_json(delivery_root(output) / 'current.json')
    second_run = _publishing_run(store)
    def fail_publish(_delivery_id, **kwargs):
        raise OSError('injected DB publish failure')
    monkeypatch.setattr(store, 'publish_delivery', fail_publish)
    with pytest.raises(OSError, match='injected DB'):
        promote_staging(output, _stage_result(output, second_run, 'two'), second_run, store)
    assert (output / 'artifacts/result.txt').read_text() == 'one'
    assert read_json(delivery_root(output) / 'current.json') == previous
    with runtime.connect() as db:
        assert db.execute('SELECT status FROM deliveries WHERE run_id=?', (second_run,)).fetchone()[0] == 'aborted'


def test_disk_full_during_public_copy_restores_old_files(tmp_path, monkeypatch):
    import flow_base.storage.delivery as delivery_module
    runtime = Runtime(tmp_path / 'share_states')
    store = RunStore(runtime)
    output = tmp_path / 'job'
    first_run = _publishing_run(store)
    promote_staging(output, _stage_result(output, first_run, 'one'), first_run, store)
    old_report = (output / 'report.json').read_bytes()
    old_pointer = read_json(delivery_root(output) / 'current.json')
    second_run = _publishing_run(store)
    real_copy = delivery_module.atomic_copy
    def disk_full(source, target):
        if Path(target).name == 'report.json':
            raise OSError('disk full')
        return real_copy(source, target)
    monkeypatch.setattr(delivery_module, 'atomic_copy', disk_full)
    with pytest.raises(OSError, match='disk full'):
        promote_staging(output, _stage_result(output, second_run, 'two'), second_run, store)
    assert (output / 'report.json').read_bytes() == old_report
    assert (output / 'artifacts/result.txt').read_text() == 'one'
    assert read_json(delivery_root(output) / 'current.json') == old_pointer


def test_failed_rollback_keeps_primary_error_and_recovery_journal(tmp_path, monkeypatch):
    import flow_base.storage.delivery as delivery_module
    runtime = Runtime(tmp_path / 'share_states')
    store = RunStore(runtime)
    output = tmp_path / 'job'
    first_run = _publishing_run(store)
    promote_staging(output, _stage_result(output, first_run, 'one'), first_run, store)
    second_run = _publishing_run(store)
    stage = _stage_result(output, second_run, 'two')
    real_rollback = delivery_module._rollback_mirror
    def fail_rollback(*args):
        raise OSError('secondary rollback failure')
    monkeypatch.setattr(delivery_module, '_rollback_mirror', fail_rollback)
    def inject(name):
        if name == 'after_new_copy:report.json':
            raise RuntimeError('primary promotion error')
    with pytest.raises(RuntimeError, match='primary promotion'):
        promote_staging(output, stage, second_run, store, checkpoint=inject)
    transaction = delivery_root(output) / 'transactions' / second_run
    assert transaction.is_dir()
    assert (transaction / 'secondary-error/error.json').is_file()
    monkeypatch.setattr(delivery_module, '_rollback_mirror', real_rollback)
    store.finish_failure(second_run)
    assert recover_transactions(output, store) == [second_run]
    assert (output / 'artifacts/result.txt').read_text() == 'one'


@pytest.mark.parametrize('cancel_at', ['before_pointer', 'after_pointer'])
def test_cancel_during_publish_rolls_back_without_false_success(tmp_path, cancel_at):
    runtime = Runtime(tmp_path / 'share_states')
    store = RunStore(runtime)
    output = tmp_path / 'job'
    first_run = _publishing_run(store)
    promote_staging(output, _stage_result(output, first_run, 'one'), first_run, store)
    old_pointer = read_json(delivery_root(output) / 'current.json')
    second_run = _publishing_run(store)
    def cancel(name):
        if name == cancel_at:
            store.request_cancel(second_run)
    with pytest.raises(FlowError) as error:
        promote_staging(output, _stage_result(output, second_run, 'two'), second_run,
                        store, checkpoint=cancel)
    assert error.value.code == 'RUN_CANCEL_REQUESTED'
    assert (output / 'artifacts/result.txt').read_text() == 'one'
    assert read_json(delivery_root(output) / 'current.json') == old_pointer
    assert store.get(second_run)['execution_status'] == 'cancelling'
    with runtime.connect() as db:
        assert db.execute('SELECT status FROM deliveries WHERE run_id=?', (second_run,)).fetchone()[0] == 'aborted'


def test_cancel_after_atomic_publish_is_state_conflict(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    store = RunStore(runtime)
    output = tmp_path / 'job'
    run_id = _publishing_run(store)
    promote_staging(output, _stage_result(output, run_id, 'one'), run_id, store)
    assert store.get(run_id)['execution_status'] == 'succeeded'
    with pytest.raises(FlowError) as error:
        store.request_cancel(run_id)
    assert error.value.code == 'STATE_CONFLICT'
    assert (output / 'artifacts/result.txt').read_text() == 'one'


def test_post_delivery_event_failure_does_not_reverse_success(workspace, monkeypatch):
    from flow_base.observability.events import EventRecorder
    monkeypatch.setattr('flow_base.engine.require_space', lambda *args: None)
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    adapter = Adapter()
    def handler(ctx, step, dependencies, target):
        target.write_text('done', encoding='utf-8')
        return target
    adapter.handlers = {'fixture.write': handler}
    monkeypatch.setattr(task, 'load', lambda runtime: adapter)
    workflow = Workflow(id='fixture', steps=[Step(id='write', handler='fixture.write')])
    real_record = EventRecorder.record
    def fail_post(self, name, **kwargs):
        if name == 'delivery.published':
            raise OSError('injected event failure')
        return real_record(self, name, **kwargs)
    monkeypatch.setattr(EventRecorder, 'record', fail_post)
    source = workspace / 'input.txt'
    source.write_text('input', encoding='utf-8')
    output = workspace / 'result'
    report = run_workflow(ws.runtime, task, workflow, source, output)
    assert report['status'] == 'complete'
    assert report['post_delivery_diagnostic']
    assert RunStore(ws.runtime).get(report['run_id'])['execution_status'] == 'succeeded'
    assert (output / 'artifacts/result.bin').read_text() == 'done'


def test_staging_keeps_domain_human_edit_guard(workspace, monkeypatch):
    monkeypatch.setattr('flow_base.engine.require_space', lambda *args: None)
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    adapter = Adapter()
    def handler(ctx, step, dependencies, target):
        target.write_text('result', encoding='utf-8')
        return target
    adapter.handlers = {'fixture.write': handler}
    def guarded_publish(terminal, destination, workflow):
        from flow_base.runtime import atomic_copy
        if (destination / 'edits.json').exists():
            raise FlowError('HUMAN_REVIEW_PRESENT', 'human edit preserved')
        atomic_copy(terminal, destination / 'result.bin')
        return {'status': 'complete'}
    monkeypatch.setattr(adapter, 'publish', guarded_publish)
    monkeypatch.setattr(task, 'load', lambda runtime: adapter)
    workflow = Workflow(id='guarded', steps=[Step(id='write', handler='fixture.write', cacheable=False)])
    source = workspace / 'input.txt'
    source.write_text('input', encoding='utf-8')
    output = workspace / 'result'
    first = run_workflow(ws.runtime, task, workflow, source, output)
    edits = output / 'artifacts/edits.json'
    edits.write_text('{"human":true}', encoding='utf-8')
    before = (output / 'report.json').read_bytes()
    with pytest.raises(FlowError) as error:
        run_workflow(ws.runtime, task, workflow, source, output)
    assert error.value.code == 'HUMAN_REVIEW_PRESENT'
    assert edits.read_text(encoding='utf-8') == '{"human":true}'
    assert (output / 'report.json').read_bytes() == before
    assert read_json(delivery_root(output) / 'current.json')['run_id'] == first['run_id']


def test_second_writer_is_busy_while_first_run_publishes(workspace, monkeypatch):
    monkeypatch.setattr('flow_base.engine.require_space', lambda *args: None)
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    entered = threading.Event()
    release = threading.Event()
    adapter = Adapter()
    def handler(ctx, step, dependencies, target):
        entered.set()
        assert release.wait(timeout=5)
        target.write_text('one', encoding='utf-8')
        return target
    adapter.handlers = {'fixture.write': handler}
    monkeypatch.setattr(task, 'load', lambda runtime: adapter)
    workflow = Workflow(id='busy', steps=[Step(id='write', handler='fixture.write', cacheable=False)])
    source = workspace / 'input.txt'
    source.write_text('input', encoding='utf-8')
    output = workspace / 'result'
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run_workflow, ws.runtime, task, workflow, source, output)
        assert entered.wait(timeout=5)
        with pytest.raises(FlowError) as error:
            run_workflow(ws.runtime, task, workflow, source, output)
        assert error.value.code == 'BUSY'
        release.set()
        report = first.result(timeout=10)
    assert read_json(output / 'report.json')['run_id'] == report['run_id']
    assert (output / 'artifacts/result.bin').read_text() == 'one'


def test_rollback_redelivers_older_version_without_rewriting_history(workspace, monkeypatch):
    monkeypatch.setattr('flow_base.engine.require_space', lambda *args: None)
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    adapter = Adapter()
    calls = []
    def handler(ctx, step, dependencies, target):
        calls.append(ctx.run_id)
        target.write_text(str(len(calls)), encoding='utf-8')
        return target
    adapter.handlers = {'fixture.write': handler}
    monkeypatch.setattr(task, 'load', lambda runtime: adapter)
    workflow = Workflow(id='rollback', steps=[Step(id='write', handler='fixture.write', cacheable=False)])
    source = workspace / 'input.txt'
    source.write_text('input', encoding='utf-8')
    output = workspace / 'result'
    run_workflow(ws.runtime, task, workflow, source, output)
    first_pointer = read_json(delivery_root(output) / 'current.json')
    run_workflow(ws.runtime, task, workflow, source, output)
    second_pointer = read_json(delivery_root(output) / 'current.json')
    assert (output / 'artifacts/result.bin').read_text() == '2'
    with pytest.raises(FlowError) as error:
        rollback_delivery(ws.runtime, output, first_pointer['delivery_id'])
    assert error.value.code == 'ROLLBACK_CONFIRM_REQUIRED'
    restored = rollback_delivery(ws.runtime, output, first_pointer['delivery_id'], confirm=True)
    assert restored['rollback_of'] == first_pointer['delivery_id']
    assert (output / 'artifacts/result.bin').read_text() == '1'
    assert (Path(delivery_root(output) / 'versions' / second_pointer['version']) / 'artifacts/result.bin').read_text() == '2'
    assert len(calls) == 2  # rollback did not recompute a workflow step
    assert read_json(delivery_root(output) / 'current.json')['run_id'] == restored['run_id']


def test_rollback_refuses_to_discard_human_changes(workspace, monkeypatch):
    monkeypatch.setattr('flow_base.engine.require_space', lambda *args: None)
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    adapter = Adapter()
    def handler(ctx, step, dependencies, target):
        target.write_text('result', encoding='utf-8')
        return target
    adapter.handlers = {'fixture.write': handler}
    monkeypatch.setattr(task, 'load', lambda runtime: adapter)
    workflow = Workflow(id='rollback', steps=[Step(id='write', handler='fixture.write')])
    source = workspace / 'input.txt'
    source.write_text('input', encoding='utf-8')
    output = workspace / 'result'
    run_workflow(ws.runtime, task, workflow, source, output)
    pointer = read_json(delivery_root(output) / 'current.json')
    (output / 'artifacts/result.bin').write_text('human edit', encoding='utf-8')
    with pytest.raises(FlowError) as error:
        rollback_delivery(ws.runtime, output, pointer['delivery_id'], confirm=True)
    assert error.value.code == 'HUMAN_REVIEW_PRESENT'
    assert (output / 'artifacts/result.bin').read_text() == 'human edit'


def test_workspace_move_preserves_delivery_history_and_rollback(workspace, monkeypatch):
    monkeypatch.setattr('flow_base.engine.require_space', lambda *args: None)
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    adapter = Adapter()
    counter = []
    def handler(ctx, step, dependencies, target):
        counter.append(1)
        target.write_text(str(len(counter)), encoding='utf-8')
        return target
    adapter.handlers = {'fixture.write': handler}
    monkeypatch.setattr(task, 'load', lambda runtime: adapter)
    workflow = Workflow(id='portable', steps=[Step(id='write', handler='fixture.write', cacheable=False)])
    source = workspace / 'input.txt'
    source.write_text('input', encoding='utf-8')
    output = workspace / 'result'
    run_workflow(ws.runtime, task, workflow, source, output)
    previous = read_json(delivery_root(output) / 'current.json')
    run_workflow(ws.runtime, task, workflow, source, output)
    moved = workspace.parent / (workspace.name + '-moved')
    shutil.copytree(workspace, moved)
    relocated_runtime = Runtime(moved / 'share_states')
    restored = rollback_delivery(relocated_runtime, moved / 'result',
                                 previous['delivery_id'], confirm=True)
    assert (moved / 'result/artifacts/result.bin').read_text() == '1'
    assert read_json(delivery_root(moved / 'result') / 'current.json')['run_id'] == restored['run_id']
