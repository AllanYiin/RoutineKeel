"""Local, journalled workflow ZIP updates. Never import code from an attachment."""
from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import sys
import uuid
import zipfile
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from filelock import FileLock, Timeout
from pydantic import ValidationError

from .runtime import FlowError, atomic_bytes, atomic_json, digest, read_json, sha256
from .workspace import Manifest

MAX_ZIP = 128 * 1024**2
DOCUMENTS = {'flow.json', 'README.md', 'LICENSE', 'AGENTS.md', 'source.zip',
             'settings.example.json', 'WORKFLOW_AUDIT.md', 'PROVENANCE.md',
             'workflow.ir.json', 'generated-handler-tests.json',
             'run_app.bat', 'run_app.sh', 'run_app.command'}
UPDATE_ID = re.compile(r'update_[a-f0-9]{32}')


def safe_path(workspace, path):
    """Reject links, junctions, and escaping paths before any write or backup read."""
    path = Path(path)
    if not path.is_relative_to(workspace.root):
        raise FlowError('PERMISSION_DENIED', '更新路徑超出工作區')
    current = workspace.root
    for part in path.relative_to(workspace.root).parts:
        current = current / part
        if current.is_symlink() or getattr(current, 'is_junction', lambda: False)():
            raise FlowError('PERMISSION_DENIED', '更新路徑不得包含連結或重新解析點')
    return path


def update_root(workspace):
    return safe_path(workspace, workspace.runtime.home / 'workflow_updates')


def ensure_no_pending(workspace):
    if (update_root(workspace) / 'pending.json').exists():
        raise FlowError('WORKFLOW_UPDATE_PENDING', '流程更新尚未結束；請由底座復原後重試')


@contextmanager
def package_lock(workspace):
    home = safe_path(workspace, workspace.runtime.home)
    home.mkdir(parents=True, exist_ok=True)
    path = safe_path(workspace, home / 'workflow-packages.lock')
    try:
        with FileLock(path, timeout=0.1):
            yield
    except Timeout as exc:
        raise FlowError('BUSY', '工作區正在執行流程或更新套件，請稍後重試') from exc


def package_operation(method):
    """Serialize Service execution/setup with updates, including detached Run reservation."""
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with package_lock(self.workspace):
            ensure_no_pending(self.workspace)
            return method(self, *args, **kwargs)
    return guarded


def _component(name):
    # Use one portable spelling on Windows, macOS, and Linux.
    if (not name or name in {'.', '..'} or name[-1:] in {'.', ' '}
            or any(ord(c) < 32 or c in '<>:"/\\|?*' for c in name)
            or name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                *{f'COM{i}' for i in range(1, 10)}, *{f'LPT{i}' for i in range(1, 10)}}):
        raise FlowError('WORKFLOW_UPDATE_INVALID', 'ZIP 或更新紀錄包含不安全檔名')
    return name


def _members(archive, *, limit, count):
    members = archive.infolist()
    if len(members) > count or sum(i.file_size for i in members) > limit:
        raise FlowError('WORKFLOW_UPDATE_INVALID', '流程壓縮包超出大小或檔案數限制')
    seen = set()
    for info in members:
        name = info.filename.rstrip('/') if info.is_dir() else info.filename
        parts = name.split('/')
        for part in parts:
            _component(part)
        key = name.casefold()
        mode = stat.S_IFMT(info.external_attr >> 16)
        if key in seen or mode not in {0, stat.S_IFREG, stat.S_IFDIR} or info.flag_bits & 1:
            raise FlowError('WORKFLOW_UPDATE_INVALID', '壓縮包含重複路徑、連結或加密檔案')
        seen.add(key)
        yield info, parts


def _package_file(name):
    parts = name.split('/')
    for part in parts:
        _component(part)
    if len(parts) == 2 and parts[0] == 'skills' and parts[1].endswith('.skill'):
        return True
    return len(parts) == 1 and (
        name in DOCUMENTS or name.endswith('.whl')
        or re.fullmatch(r'handler_[a-z][a-z0-9_-]{0,31}\.py', name)
    )


def _read_archive(path):
    try:
        if not path.is_file() or path.stat().st_size > MAX_ZIP:
            raise FlowError('WORKFLOW_UPDATE_INVALID', '請提供不超過 128 MiB 的流程 ZIP')
        raw = path.read_bytes()
        files, modes, roots = {}, {}, set()
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for info, parts in _members(archive, limit=MAX_ZIP, count=256):
                # Current releases overlay workflows/FLOW_*/; keep accepting
                # historical single-folder attachments without relaxing traversal checks.
                if parts[0] == 'workflows':
                    if info.is_dir() and len(parts) == 1:
                        continue
                    if len(parts) < 2 or not parts[1].startswith('FLOW_'):
                        raise FlowError('WORKFLOW_UPDATE_INVALID', '流程 ZIP 的 workflows 路徑不合法')
                    prefix = parts[:2]
                    content = parts[2:]
                else:
                    prefix = parts[:1]
                    content = parts[1:]
                roots.add('/'.join(prefix))
                if info.is_dir():
                    if content and content != ['skills']:
                        raise FlowError('WORKFLOW_UPDATE_INVALID', '流程 ZIP 只接受單一資料夾內的套件檔')
                    continue
                if not content:
                    raise FlowError('WORKFLOW_UPDATE_INVALID', '流程 ZIP 必須包含單一流程資料夾')
                name = '/'.join(content)
                if not _package_file(name):
                    raise FlowError('WORKFLOW_UPDATE_INVALID', '流程 ZIP 含非套件檔案；不得夾帶設定、成果或環境')
                files[name] = archive.read(info)
                modes[name] = 0o755 if name.endswith(('.sh', '.command')) else 0o644
        if len(roots) != 1 or 'flow.json' not in files:
            raise FlowError('WORKFLOW_UPDATE_INVALID', 'ZIP 缺少唯一的流程 flow.json；不接受底座 ZIP')
        manifest = Manifest.model_validate_json(files['flow.json'])
        wheels = {name for name in files if name.endswith('.whl')}
        if wheels != {manifest.package} or hashlib.sha256(files[manifest.package]).hexdigest() != manifest.sha256:
            raise FlowError('WORKFLOW_UPDATE_INVALID', '流程 wheel 缺失或 SHA-256 不符')
        with zipfile.ZipFile(io.BytesIO(files[manifest.package])) as wheel:
            names = set()
            for info, parts in _members(wheel, limit=32 * 1024**2, count=3000):
                if parts[0] != manifest.python_module and not (
                        parts[0].startswith(manifest.python_module + '-') and parts[0].endswith('.dist-info')):
                    raise FlowError('WORKFLOW_UPDATE_INVALID', 'wheel 只能包含本流程的 Python namespace')
                if not info.is_dir():
                    wheel.read(info)  # CRC validation, without importing or running code.
                    names.add(info.filename)
            if manifest.python_module + '/adapter.py' not in names:
                raise FlowError('WORKFLOW_UPDATE_INVALID', 'wheel 缺少流程 Adapter')
        return manifest, files, modes, hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError, KeyError, ValidationError, zipfile.BadZipFile, RuntimeError) as exc:
        raise FlowError('WORKFLOW_UPDATE_INVALID', '流程 ZIP 損壞或不符合套件契約') from exc


def _file_state(workspace, path):
    safe_path(workspace, path)
    if path.exists() and (not path.is_file() or path.stat().st_size > MAX_ZIP):
        raise FlowError('WORKFLOW_UPDATE_CONFLICT', '套件目標不是一般檔案或超出大小限制')
    return {'sha256': sha256(path), 'mode': stat.S_IMODE(path.stat().st_mode)} if path.exists() else None


def _prepare(workspace, path, task_id=None):
    manifest, files, modes, archive_hash = _read_archive(path)
    tasks = workspace.discover(read_only=True)
    if task_id:
        task = workspace.task(task_id, read_only=True)
    else:
        matches = [t for t in tasks.values() if (
            t.manifest.id == manifest.id if manifest.id else t.manifest.python_module == manifest.python_module)]
        if len(matches) != 1:
            raise FlowError('WORKFLOW_UPDATE_CONFLICT', '附件無法唯一對應已安裝流程；更新不會自動新增流程')
        task = matches[0]
    old = task.manifest
    if (manifest.id is not None and manifest.id != old.id) or manifest.python_module != old.python_module:
        raise FlowError('WORKFLOW_UPDATE_CONFLICT', '附件的流程識別碼或 Python module 與目標不同')
    # Legacy manifests can omit id; bind the installed identity before switching versions.
    if manifest.id is None:
        manifest = manifest.model_copy(update={'id': old.id})
        files['flow.json'] = (json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2) + '\n').encode()
    if tuple(map(int, manifest.version.split('.'))) < tuple(map(int, old.version.split('.'))):
        raise FlowError('WORKFLOW_UPDATE_CONFLICT', '更新不能降版；需要退回時請使用原更新紀錄回復')
    states = {name: _file_state(workspace, task.folder / name) for name in files}
    # Include current manifest and wheel even when their names differ from the attachment.
    installed = {name: _file_state(workspace, task.folder / name) for name in {'flow.json', old.package}}
    if installed[old.package] is None or installed[old.package]['sha256'] != old.sha256:
        raise FlowError('WORKFLOW_UPDATE_CONFLICT', '既有流程 wheel 損壞；保留現況，不能直接覆蓋')
    changed = [name for name in sorted(files) if not states[name] or
               states[name]['sha256'] != hashlib.sha256(files[name]).hexdigest()]
    same_version = manifest.version == old.version
    if same_version and (changed or manifest != old):
        raise FlowError('WORKFLOW_UPDATE_CONFLICT', '相同版本的內容不同；請提供新版本號的流程包')
    basis = {'archive_sha256': archive_hash, 'folder': task.folder.name,
             'workflow_id': old.id, 'before': states, 'installed': installed}
    plan = {'status': 'already_current' if same_version else 'ready',
            'workflow_id': old.id, 'flow_id': task.flow_id, 'title': manifest.title,
            'folder': task.folder.name, 'from_version': old.version, 'to_version': manifest.version,
            'archive_sha256': archive_hash, 'plan_hash': digest(basis), 'files_changed': changed,
            'preserved': ['settings.json', 'outputs/', 'share_states/', 'share_logs/', 'other_local_files'],
            'executes_package_code': False}
    return plan, files, modes, states


def preview_update(workspace, path, task_id=None):
    ensure_no_pending(workspace)
    return _prepare(workspace, path, task_id)[0]


def _idle(workspace):
    workspace.runtime.initialize()
    with closing(workspace.runtime.connect()) as db:
        active = db.execute("SELECT 1 FROM run_executions WHERE execution_status IN "
                            "('created','queued','preparing','running','publishing','cancelling') LIMIT 1").fetchone()
        queued = db.execute("SELECT 1 FROM execution_queue WHERE status NOT IN "
                            "('succeeded','failed','cancelled','interrupted') LIMIT 1").fetchone()
    if active or queued:
        raise FlowError('BUSY', '工作區仍有執行中或排隊工作；完成或取消後才能更新／回復')


def _write_file(path, body, mode):
    atomic_bytes(path, body)
    path.chmod(mode)


def _receipt(workspace, update_id):
    if not isinstance(update_id, str) or not UPDATE_ID.fullmatch(update_id):
        raise FlowError('WORKFLOW_UPDATE_INVALID', '更新編號格式不正確')
    folder = safe_path(workspace, update_root(workspace) / update_id)
    try:
        record = read_json(safe_path(workspace, folder / 'record.json'))
        if record['update_id'] != update_id or record['schema_version'] != 1:
            raise ValueError('identity')
        _component(record['folder'])
        if record['folder'].startswith('.') or record['folder'] in {'flow_base', 'share_states', 'share_logs'}:
            raise ValueError('folder')
        if record['status'] not in {'prepared', 'applied', 'restoring', 'rolled_back'}:
            raise ValueError('status')
        if not 1 <= len(record['files']) <= 256 or 'flow.json' not in record['files']:
            raise ValueError('files')
        for name, entry in record['files'].items():
            if not _package_file(name):
                raise ValueError('file')
            for phase in ('before', 'after'):
                expected = entry[phase]
                if expected is None and phase == 'before':
                    continue
                if (not re.fullmatch(r'[a-f0-9]{64}', expected['sha256'])
                        or type(expected['mode']) is not int or not 0 <= expected['mode'] <= 0o777):
                    raise ValueError('state')
                backup = safe_path(workspace, folder / phase / name)
                if not backup.is_file() or backup.stat().st_size > MAX_ZIP or sha256(backup) != expected['sha256']:
                    raise ValueError('backup')
        return folder, record
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise FlowError('WORKFLOW_UPDATE_INVALID', '更新紀錄或備份損壞；保留現況，不能猜測回復') from exc


def _restore(workspace, update_id):
    folder, record = _receipt(workspace, update_id)
    target = safe_path(workspace, workspace.workflows_root / record['folder'])
    # Check the entire transaction before the first write; preserve later local edits.
    for name, entry in record['files'].items():
        observed = _file_state(workspace, target / name)
        allowed = [entry['before'], entry['after']] if record['status'] in {'prepared', 'restoring'} else [entry['after']]
        if record['status'] == 'rolled_back':
            allowed = [entry['before']]
        if not any((observed or {}).get('sha256') == (item or {}).get('sha256') for item in allowed):
            raise FlowError('WORKFLOW_UPDATE_CONFLICT', '套件檔案已被後續修改；不覆寫現有內容')
    pending = update_root(workspace) / 'pending.json'
    if pending.exists() and read_json(pending).get('update_id') != update_id:
        raise FlowError('WORKFLOW_UPDATE_CONFLICT', '另一筆更新尚未結束，不能回復此版本')
    atomic_json(pending, {'update_id': update_id})
    record['status'] = 'restoring'
    atomic_json(folder / 'record.json', record)
    # Restore manifest last, just as when applying an update.
    for name in sorted(record['files'], key=lambda n: (n == 'flow.json', n)):
        entry = record['files'][name]
        path = safe_path(workspace, target / name)
        if entry['before'] is None:
            if path.exists():
                path.unlink()
        else:
            _write_file(path, (folder / 'before' / name).read_bytes(), entry['before']['mode'])
    for name, entry in record['files'].items():
        observed = _file_state(workspace, target / name)
        if (observed or {}).get('sha256') != (entry['before'] or {}).get('sha256'):
            raise FlowError('WORKFLOW_UPDATE_INVALID', '回復後檔案驗證失敗；保留紀錄供重試')
    record['status'] = 'rolled_back'
    atomic_json(folder / 'record.json', record)
    pending.unlink()
    return {'status': 'rolled_back', 'update_id': update_id, 'workflow_id': record['workflow_id'],
            'version': record['from_version'], 'backup_path': str(folder), 'restart_required': True}


def _recover(workspace):
    pending = safe_path(workspace, update_root(workspace) / 'pending.json')
    if not pending.exists():
        return {'status': 'no_pending_update'}
    try:
        update_id = read_json(pending)['update_id']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise FlowError('WORKFLOW_UPDATE_INVALID', '未完成更新的索引損壞') from exc
    return _restore(workspace, update_id)


def recover_update(workspace):
    with package_lock(workspace):
        _idle(workspace)
        return _recover(workspace)


def rollback_update(workspace, update_id):
    with package_lock(workspace):
        ensure_no_pending(workspace)
        _idle(workspace)
        return _restore(workspace, update_id)


def apply_update(workspace, path, plan_hash, task_id=None):
    with package_lock(workspace):
        _idle(workspace)
        # Retrying an interrupted update first restores the recorded old version.
        _recover(workspace)
        plan, files, modes, states = _prepare(workspace, path, task_id)
        if plan['plan_hash'] != plan_hash:
            raise FlowError('WORKFLOW_UPDATE_CONFLICT', '附件或已安裝內容已變更；請重新預覽更新')
        if plan['status'] == 'already_current':
            return plan
        update_id = 'update_' + uuid.uuid4().hex
        folder = safe_path(workspace, update_root(workspace) / update_id)
        target = safe_path(workspace, workspace.workflows_root / plan['folder'])
        entries = {}
        # All backup/staging writes complete before publishing the pending pointer.
        for name, body in files.items():
            after = {'sha256': hashlib.sha256(body).hexdigest(), 'mode': modes[name]}
            entries[name] = {'before': states[name], 'after': after}
            if states[name]:
                _write_file(folder / 'before' / name, (target / name).read_bytes(), states[name]['mode'])
            _write_file(folder / 'after' / name, body, modes[name])
        record = {**plan, 'schema_version': 1, 'update_id': update_id, 'status': 'prepared',
                  'created_at': datetime.now(timezone.utc).isoformat(), 'files': entries}
        atomic_json(folder / 'record.json', record)
        _receipt(workspace, update_id)
        # Detect non-cooperating local edits while backups were being written.
        if _prepare(workspace, path, task_id)[0]['plan_hash'] != plan_hash:
            raise FlowError('WORKFLOW_UPDATE_CONFLICT', '備份期間附件或流程已變更；請重新預覽')
        pending = update_root(workspace) / 'pending.json'
        atomic_json(pending, {'update_id': update_id})
        try:
            for name in sorted(files, key=lambda n: (n == 'flow.json', n)):
                _write_file(target / name, files[name], modes[name])
            for name, entry in entries.items():
                if sha256(target / name) != entry['after']['sha256']:
                    raise FlowError('WORKFLOW_UPDATE_INVALID', '更新後檔案驗證失敗')
            record['status'] = 'applied'
            atomic_json(folder / 'record.json', record)
            pending.unlink()
        except BaseException:
            # A kill or another write failure leaves pending intact for recovery.
            _restore(workspace, update_id)
            raise
        manifest = Manifest.model_validate_json(files['flow.json'])
        return {**plan, 'status': 'updated', 'update_id': update_id, 'backup_path': str(folder),
                'restart_required': manifest.python_module in sys.modules}
