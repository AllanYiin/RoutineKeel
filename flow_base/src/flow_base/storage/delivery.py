"""Same-filesystem staging for immutable delivery versions."""
from __future__ import annotations

import json
import os
import shutil
import time
from contextlib import closing
from pathlib import Path, PurePosixPath

from flow_base.observability.recovery import SecondaryOperation

from ..outputs import reject_links, validate_tree
from ..runtime import FlowError, atomic_copy, atomic_json, read_json, sha256

VERSION_MANIFEST = 'delivery-manifest.json'


def delivery_root(output):
    output = Path(output).absolute()
    root = output.parent / ('.' + output.name + '.deliveries')
    reject_links(root)
    return root


def staging_path(output, run_id):
    if not run_id.startswith('run_') or not run_id[4:].isalnum():
        raise FlowError('DELIVERY_RUN_ID', '無效的 Run 身分')
    target = delivery_root(output) / 'staging' / run_id
    reject_links(target)
    return target


def prepare_staging(output, run_id):
    """Clone the old result so Adapter checks still see human edits, without modifying it."""
    output = Path(output).absolute()
    validate_tree(output)
    target = staging_path(output, run_id)
    if target.exists():
        raise FlowError('DELIVERY_STAGE_EXISTS', '此 Run 的 staging 已存在', run_id=run_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if not output.is_dir():
            raise FlowError('OUTPUT_EXISTS', '正式輸出位置不是資料夾')
        shutil.copytree(output, target, symlinks=False)
    else:
        target.mkdir()
    validate_tree(target)
    return target


def _files(root):
    return {path.relative_to(root).as_posix(): path for path in root.rglob('*')
            if path.is_file() and path.relative_to(root).as_posix() != VERSION_MANIFEST}


def _child(root, relative):
    path = PurePosixPath(relative)
    if not relative or path.is_absolute() or '..' in path.parts or '\\' in relative or ':' in relative:
        raise FlowError('DELIVERY_PATH', '交付清單包含不安全的相對路徑', path=relative)
    target = Path(root).joinpath(*path.parts)
    reject_links(target)
    if not target.resolve().is_relative_to(Path(root).resolve()):
        raise FlowError('DELIVERY_PATH', '交付檔案越出工作目錄', path=relative)
    return target


def write_version_manifest(stage, run_id):
    stage = Path(stage)
    validate_tree(stage)
    report = stage / 'report.json'
    if not report.is_file() or read_json(report).get('run_id') != run_id:
        raise FlowError('DELIVERY_INVALID', 'staging 缺少本次 Run 的正式報告', run_id=run_id)
    index = stage / 'deliverables.json'
    if index.is_file():
        for item in read_json(index).get('files', []):
            path = _child(stage, item['path'])
            if not path.is_file() or path.stat().st_size != item['size_bytes'] or sha256(path) != item['sha256']:
                raise FlowError('DELIVERY_INVALID', '交付索引與 staging 檔案不一致', path=item['path'])
    rows = []
    for name, path in sorted(_files(stage).items()):
        rows.append({'path': name, 'size_bytes': path.stat().st_size, 'sha256': sha256(path)})
    if not any(row['path'] == 'report.json' for row in rows):
        raise FlowError('DELIVERY_INVALID', 'staging 缺少 report.json')
    manifest = {'schema_version': 1, 'run_id': run_id, 'files': rows}
    atomic_json(stage / VERSION_MANIFEST, manifest)
    return stage / VERSION_MANIFEST


def verify_version(version):
    version = Path(version)
    validate_tree(version)
    manifest_path = version / VERSION_MANIFEST
    try:
        manifest = read_json(manifest_path)
        if manifest.get('schema_version') != 1:
            return False
        rows = manifest['files']
        known = {row['path'] for row in rows}
        if len(known) != len(rows) or known != set(_files(version)):
            return False
        for row in rows:
            path = _child(version, row['path'])
            if not path.is_file() or path.stat().st_size != row['size_bytes'] or sha256(path) != row['sha256']:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError, FlowError):
        return False


def commit_graph_preview(runtime, folder, run_id):
    from ..core.lifecycle import manifest_reference, utc_now
    manifest=folder/VERSION_MANIFEST
    if not verify_version(folder):
        raise FlowError("DELIVERY_INVALID","預覽產物未通過指紋檢查")
    report=read_json(folder/"report.json")
    if report["run_id"]!=run_id or report.get("graph_delivery_status")!="withheld":
        raise FlowError("DELIVERY_INVALID","預覽資料與執行版本不符")
    with closing(runtime.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        existing=db.execute("SELECT manifest_sha256 FROM graph_previews WHERE run_id=?",(run_id,)).fetchone()
        checksum=sha256(manifest)
        if existing:
            if existing[0]!=checksum: raise FlowError("DELIVERY_INVALID","既有預覽已變更")
            return
        updated=db.execute("""UPDATE run_executions SET execution_status='succeeded',state_version=state_version+1,
             result_status=?,delivery_status='withheld',ended_at=? WHERE run_id=? AND execution_status='publishing'""",
             (report["graph_result_status"],utc_now(),run_id))
        if updated.rowcount!=1: raise FlowError("STATE_CONFLICT","工作狀態已變更，未登錄預覽")
        db.execute("INSERT INTO graph_previews VALUES (?,?,?,?)",(run_id,report["blueprint"].get("assessment_id"),
             json.dumps(manifest_reference(runtime,manifest)),checksum))
        db.execute("UPDATE runs SET status='complete',finished=? WHERE run_id=?",
                   (time.time(),run_id))


def recover_graph_previews(runtime):
    with closing(runtime.connect()) as db:
        runs=db.execute("""SELECT r.run_id,r.output FROM runs r JOIN run_executions x USING(run_id)
                         WHERE x.execution_status='publishing' AND r.output IS NOT NULL""").fetchall()
    for row in runs:
        try:
            folder=delivery_root(row["output"])/"previews"/row["run_id"]
            if (folder/VERSION_MANIFEST).is_file():
                commit_graph_preview(runtime,folder,row["run_id"])
        except (FlowError,OSError) as exc:
            # Preserve a failed recovery point without preventing unrelated jobs from opening.
            atomic_json(runtime.home/"preview_recovery"/(row["run_id"]+".json"),
                        {"run_id":row["run_id"],"status":"needs_attention","code":getattr(exc,"code",type(exc).__name__)})


def _snapshot_legacy(output, versions, run_id):
    if not output.is_dir() or not _files(output):
        return None
    destination = versions / ('legacy-' + run_id)
    if destination.exists():
        raise FlowError('DELIVERY_VERSION_EXISTS', '舊版快照目錄已存在')
    shutil.copytree(output, destination, symlinks=False)
    rows = [{'path': name, 'size_bytes': path.stat().st_size, 'sha256': sha256(path)}
            for name, path in sorted(_files(destination).items())]
    old_report = read_json(destination / 'report.json') if (destination / 'report.json').is_file() else {}
    atomic_json(destination / VERSION_MANIFEST,
                {'schema_version': 1, 'run_id': old_report.get('run_id', 'legacy'), 'files': rows})
    if not verify_version(destination):
        raise FlowError('DELIVERY_INVALID', '舊成果快照校驗失敗')
    return destination


def _safe_remove_generated(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root) or path == root:
        raise FlowError('DELIVERY_PATH', '拒絕清理交付目錄以外的路徑')
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _rollback_mirror(output, transaction, marker):
    output = Path(output)
    backups = transaction / 'rollback'
    old_names = set(marker['old_files'])
    for name in reversed(marker['paths']):
        public = _child(output, name)
        backup = _child(backups, name)
        if backup.is_file():
            public.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, public)
        elif name not in old_names and public.is_file():
            public.unlink()
    pointer = delivery_root(output) / 'current.json'
    old_pointer = transaction / 'previous-pointer.json'
    if old_pointer.is_file():
        os.replace(old_pointer, pointer)
    elif marker['previous_pointer'] is not None:
        if not pointer.is_file() or read_json(pointer) != marker['previous_pointer']:
            atomic_json(pointer, marker['previous_pointer'])
    elif pointer.is_file():
        pointer.unlink()


def recover_transactions(output, store):
    """Rollback any incomplete mirror; never trust an uncommitted version."""
    output = Path(output).absolute()
    root = delivery_root(output)
    transactions = root / 'transactions'
    if not transactions.is_dir():
        return []
    recovered = []
    for transaction in sorted(transactions.iterdir()):
        if not transaction.is_dir() or transaction.is_symlink():
            continue
        marker_path = transaction / 'transaction.json'
        if not marker_path.is_file():
            continue
        marker = read_json(marker_path)
        from ..core.lifecycle import ACTIVE
        run = store.get(marker['run_id'])
        if run and run['execution_status'] in ACTIVE:
            continue
        with store.runtime.connect() as db:
            row = db.execute('SELECT status FROM deliveries WHERE delivery_id=?', (marker['delivery_id'],)).fetchone()
        if row and row['status'] == 'published':
            _safe_remove_generated(transaction, root)
            continue
        _rollback_mirror(output, transaction, marker)
        if row and row['status'] == 'staged':
            store.abort_delivery(marker['delivery_id'])
        _safe_remove_generated(transaction, root)
        recovered.append(marker['run_id'])
    return recovered


def promote_staging(output, stage, run_id, store, *, checkpoint=None):
    """Verify, version, mirror with rollback, then atomically switch pointer and DB."""
    output = Path(output).absolute()
    stage = Path(stage).absolute()
    root = delivery_root(output)
    if stage != staging_path(output, run_id):
        raise FlowError('DELIVERY_PATH', 'staging 路徑與本次 Run 不符')
    if checkpoint is None:
        checkpoint = lambda name: None
    write_version_manifest(stage, run_id)
    if not verify_version(stage):
        raise FlowError('DELIVERY_INVALID', 'staging 產物未通過完整性檢查')
    checkpoint('staging_verified')
    versions = root / 'versions'
    versions.mkdir(parents=True, exist_ok=True)
    version = versions / run_id
    if version.exists():
        raise FlowError('DELIVERY_VERSION_EXISTS', '本次 Run 的版本目錄已存在')
    os.replace(stage, version)
    checkpoint('version_renamed')
    if not verify_version(version):
        raise FlowError('DELIVERY_INVALID', '版本化後產物不完整')
    delivery_id = store.stage_delivery(run_id, version / VERSION_MANIFEST)
    created_transaction = False
    existing_transaction = False
    try:
        checkpoint('db_staged')
        pointer = root / 'current.json'
        previous_pointer = read_json(pointer) if pointer.is_file() else None
        legacy_snapshot = None
        if previous_pointer is None:
            legacy_snapshot = _snapshot_legacy(output, versions, run_id)
            if legacy_snapshot:
                store.relocate_published_manifests(store.get(run_id)['job_id'], output, legacy_snapshot)
        checkpoint('previous_version_saved')
        transaction = root / 'transactions' / run_id
        if transaction.exists():
            existing_transaction = True
            raise FlowError('DELIVERY_TRANSACTION_EXISTS', '此 Run 的交付交易已存在')
        transaction.mkdir(parents=True)
        created_transaction = True
        if previous_pointer is not None:
            atomic_json(transaction / 'previous-pointer.json', previous_pointer)
        old_files = _files(output) if output.is_dir() else {}
        new_files = _files(version)
        names = sorted(set(old_files) | set(new_files))
        marker = {'schema_version': 1, 'run_id': run_id, 'delivery_id': delivery_id,
                  'version': version.name, 'old_files': sorted(old_files),
                  'paths': names, 'previous_pointer': previous_pointer,
                  'legacy_snapshot': legacy_snapshot.name if legacy_snapshot else None}
        atomic_json(transaction / 'transaction.json', marker)
        checkpoint('transaction_recorded')
    except BaseException:
        if not existing_transaction:
            with SecondaryOperation('store.abort_delivery', catch=BaseException):
                store.abort_delivery(delivery_id)
        if created_transaction and transaction.exists():
            with SecondaryOperation('_safe_remove_generated', catch=BaseException):
                _safe_remove_generated(transaction, root)
        raise
    try:
        output.mkdir(parents=True, exist_ok=True)
        backups = transaction / 'rollback'
        for name in names:
            public = _child(output, name)
            backup = _child(backups, name)
            checkpoint('before_mirror:' + name)
            if public.is_file():
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(public, backup)
            checkpoint('after_old_move:' + name)
            if name in new_files:
                atomic_copy(new_files[name], public)
            checkpoint('after_new_copy:' + name)
        checkpoint('mirror_complete')
        current = {'schema_version': 1, 'run_id': run_id, 'delivery_id': delivery_id,
                   'version': version.name, 'manifest_sha256': sha256(version / VERSION_MANIFEST)}
        checkpoint('before_pointer')
        status = store.get(run_id)['execution_status']
        if status != 'publishing':
            raise FlowError('RUN_CANCEL_REQUESTED' if status == 'cancelling' else 'STATE_CONFLICT',
                            '發布前收到取消或狀態已變更', run_id=run_id)
        atomic_json(pointer, current)
        checkpoint('after_pointer')
        report = read_json(version / 'report.json')
        report_status = str(report.get('graph_result_status',report.get('status', 'unknown'))).lower()
        if report.get("graph_delivery_status")=="eligible":
            store.publish_delivery(delivery_id, complete_run=True, result_status=report_status, quality=report["blueprint"])
        else:
            store.publish_delivery(delivery_id, complete_run=True, result_status=report_status)
    except BaseException as original:
        restored = False
        with SecondaryOperation('_rollback_mirror', catch=BaseException) as rollback_mirror_outcome:
            _rollback_mirror(output, transaction, marker)
            store.abort_delivery(delivery_id)
            restored = True
        if rollback_mirror_outcome.error is not None:
            secondary = rollback_mirror_outcome.error
            with SecondaryOperation('record mirror rollback failure', catch=BaseException):
                from ..diagnostics import record_exception
                primary = record_exception(original, transaction / 'primary-error', runtime=store.runtime, run_id=run_id)
                record_exception(secondary, transaction / 'secondary-error', runtime=store.runtime,
                                 parent_error_id=primary['error_id'], run_id=run_id)
        if restored:
            try:
                _safe_remove_generated(transaction, root)
            except OSError:
                pass
        raise
    with SecondaryOperation('_safe_remove_generated', catch=BaseException):
        _safe_remove_generated(transaction, root)
    return {'delivery_id': delivery_id, 'version': str(version), 'pointer': str(pointer)}


def delivery_gc_preview(runtime, output):
    """Only unreferenced, non-legacy version folders are candidates."""
    runtime.initialize()
    root = delivery_root(output)
    versions = root / 'versions'
    if not versions.is_dir():
        return {'candidates': [], 'bytes': 0}
    if versions.is_symlink() or (hasattr(versions, 'is_junction') and versions.is_junction()):
        raise FlowError('GC_PATH', '交付版本根目錄不可為連結')
    pointer = read_json(root / 'current.json') if (root / 'current.json').is_file() else {}
    with closing(runtime.connect()) as db:
        rows = [row[0] for row in db.execute('SELECT a.manifest_json FROM deliveries d JOIN artifact_sets a ON a.artifact_set_id=d.artifact_set_id')]
        active = {row[0] for row in db.execute("SELECT run_id FROM run_executions WHERE execution_status IN ('created','queued','preparing','running','publishing','cancelling')")}
    referenced = set()
    from ..core.lifecycle import resolve_manifest_reference
    for payload in rows:
        try:
            path = resolve_manifest_reference(runtime, payload)
            if path.is_relative_to(versions.resolve()):
                referenced.add(path.relative_to(versions.resolve()).parts[0])
        except (ValueError, KeyError, TypeError, IndexError, FlowError):
            continue
    referenced.add(pointer.get('version'))
    referenced.update(active)
    transactions = root / 'transactions'
    if transactions.is_dir():
        for marker in transactions.glob('*/transaction.json'):
            try:
                referenced.add(read_json(marker).get('version'))
            except (OSError, ValueError):
                continue
    candidates = []
    for folder in sorted(versions.iterdir()):
        if (not folder.is_dir() or folder.is_symlink() or
                (hasattr(folder, 'is_junction') and folder.is_junction()) or
                folder.name.startswith('legacy-') or folder.name in referenced):
            continue
        if not folder.name.startswith('run_'):
            continue
        size = sum(path.stat().st_size for path in folder.rglob('*') if path.is_file() and not path.is_symlink())
        candidates.append({'path': str(folder), 'bytes': size})
    return {'candidates': candidates, 'bytes': sum(item['bytes'] for item in candidates)}


def delivery_gc_apply(runtime, output, *, confirm=False):
    if not confirm:
        raise FlowError('GC_CONFIRM_REQUIRED', '清理交付版本前須明確確認')
    root = delivery_root(output)
    with runtime.lock('output:' + str(Path(output).resolve()), timeout=30):
        preview = delivery_gc_preview(runtime, output)
        removed = []
        for item in preview['candidates']:
            candidate = Path(item['path']).resolve()
            if not candidate.is_relative_to((root / 'versions').resolve()):
                raise FlowError('GC_PATH', '拒絕清理交付版本目錄以外的路徑')
            if any(path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())
                   for path in candidate.rglob('*')):
                raise FlowError('GC_PATH', '交付版本含連結，拒絕清理')
            _safe_remove_generated(candidate, root)
            removed.append(item)
        return {'removed': removed, 'bytes': sum(item['bytes'] for item in removed)}


def rollback_delivery(runtime, output, target_delivery_id, *, confirm=False):
    """Re-deliver an immutable older version as a new Run, preserving all history."""
    if not confirm:
        raise FlowError('ROLLBACK_CONFIRM_REQUIRED', '回復舊版成果前須明確確認')
    from ..core.lifecycle import RunStore, resolve_manifest_reference
    from ..execution.supervisor import Supervisor
    from ..observability.events import EventRecorder
    from ..outputs import write_delivery_index
    from ..runtime import digest

    output = Path(output).resolve()
    root = delivery_root(output)
    store = RunStore(runtime)
    with closing(runtime.connect()) as db:
        target = db.execute('SELECT d.job_id,d.status,r.flow_id,a.manifest_json,a.manifest_sha256 FROM deliveries d JOIN artifact_sets a ON a.artifact_set_id=d.artifact_set_id JOIN runs r ON r.run_id=d.run_id WHERE d.delivery_id=?',
                            (target_delivery_id,)).fetchone()
    if target is None or target['status'] not in {'published', 'superseded'}:
        raise FlowError('DELIVERY_UNKNOWN', '找不到可回復的已發布版本')
    version = resolve_manifest_reference(runtime, target['manifest_json']).parent
    if not version.is_relative_to((root / 'versions').resolve()) or not verify_version(version):
        raise FlowError('DELIVERY_INVALID', '舊版成果不完整，不能回復')
    with runtime.lock('output:' + str(output), timeout=30):
        recover_transactions(output, store)
        pointer = read_json(root / 'current.json')
        current = root / 'versions' / pointer['version']
        if not verify_version(current):
            raise FlowError('DELIVERY_INVALID', '目前正式版本不完整，拒絕回復')
        current_files = _files(current)
        public_files = _files(output)
        if set(current_files) != set(public_files) or any(sha256(current_files[name]) != sha256(public_files[name])
                                                         for name in current_files):
            raise FlowError('HUMAN_REVIEW_PRESENT', '目前成果已有額外修改，請先另存再回復舊版')
        run_id = runtime.start_run(target['flow_id'], 'rollback:' + target_delivery_id, output,
                                   job_id=target['job_id'],
                                   spec_hash=digest({'rollback_of': target_delivery_id,
                                                     'manifest_sha256': target['manifest_sha256']}))
        recorder = EventRecorder(runtime, run_id, runtime.logs / target['flow_id'] / run_id)
        try:
            store.transition(run_id, 'created', 'queued')
            with Supervisor(runtime, run_id):
                recorder.record('run.claimed')
                recorder.record('run.preparing')
                store.transition(run_id, 'preparing', 'running')
                recorder.record('run.started')
                stage = staging_path(output, run_id)
                stage.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(version, stage, ignore=shutil.ignore_patterns(VERSION_MANIFEST))
                report = read_json(stage / 'report.json')
                report.update(run_id=run_id, rollback_of=target_delivery_id, output=str(output))
                atomic_json(stage / 'report.json', report)
                write_delivery_index(stage, report)
                recorder.record('delivery.staged', attributes={'reason': 'rollback'})
                store.transition(run_id, 'running', 'publishing')
                delivered = promote_staging(output, stage, run_id, store)
                result = {'run_id': run_id, 'delivery_id': delivered['delivery_id'],
                          'rollback_of': target_delivery_id, 'output': str(output)}
                with SecondaryOperation('record published delivery') as publication_update:
                    if store.get(run_id)['execution_status'] != 'succeeded':
                        raise FlowError('DELIVERY_INCOMPLETE', '回復版本尚未正式發布', run_id=run_id)
                    runtime.update_run(run_id, report.get('status', 'complete'),
                                       job_id=target['job_id'], finished=True)
                    recorder.record('delivery.published', attributes={'reason': 'rollback'})
                    recorder.record('run.finished', attributes={'result_status': str(report.get('status', 'unknown')).lower()})
                if publication_update.error is not None:
                    post_error = publication_update.error
                    from ..diagnostics import record_exception
                    result['post_delivery_diagnostic'] = record_exception(
                        post_error, runtime.logs / target['flow_id'] / run_id / 'post_delivery',
                        runtime=runtime, run_id=run_id).get('diagnostic_json')
                return result
        except BaseException as exc:
            from ..diagnostics import record_exception
            from ..observability.diagnostics import guard_secondary
            with SecondaryOperation('recorder.record'):
                recorder.record('run.failed', severity='ERROR', attributes={'code': type(exc).__name__})
            record = record_exception(exc, runtime.logs / target['flow_id'] / run_id,
                                      runtime=runtime, run_id=run_id)
            guard_secondary(exc, record, 'rollback_run_failure',
                            lambda: store.finish_failure(run_id, error_id=record['error_id']), runtime=runtime)
            raise
