"""Offline diagnostic queries for the CLI and future service adapters."""
from __future__ import annotations

import os
import json
import errno
import re
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from ..runtime import FlowError, read_json
from .diagnostics import safe_export
from .errors import ALIASES, STANDARD, explain_error

RUN_ID = re.compile(r'^run_[a-f0-9]{32}$')
ERROR_ID = re.compile(r'^error_[a-f0-9]{32}$')


def _run(runtime, run_id):
    if not RUN_ID.fullmatch(run_id):
        raise FlowError('RUN_UNKNOWN', '請提供有效的 Run ID')
    runtime.initialize()
    with closing(runtime.connect()) as db:
        row = db.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone()
        execution = db.execute('SELECT * FROM run_executions WHERE run_id=?', (run_id,)).fetchone()
    if row is None:
        raise FlowError('RUN_UNKNOWN', '找不到指定 Run', run_id=run_id)
    return dict(row), dict(execution) if execution else None


def _logdir(runtime, flow_id, run_id):
    folder = (runtime.logs / str(flow_id) / run_id).resolve()
    if not folder.is_relative_to(runtime.logs.resolve()):
        raise FlowError('PERMISSION_DENIED', 'Run 日誌路徑越出工作區')
    return folder


def _diagnostic(runtime, *, run_id=None, error_id=None):
    runtime.initialize()
    with closing(runtime.connect()) as db:
        if error_id:
            row = db.execute('SELECT * FROM diagnostics WHERE error_id=?', (error_id,)).fetchone()
        else:
            row = db.execute('SELECT * FROM diagnostics WHERE run_id=? ORDER BY occurred_at DESC LIMIT 1', (run_id,)).fetchone()
    if row is None:
        if error_id:
            for path in runtime.logs.glob(f'launcher/launch_*/{error_id}/error.json'):
                if path.is_file() and path.resolve().is_relative_to(runtime.logs.resolve()):
                    try:
                        payload = read_json(path)
                        if payload.get('error_id') == error_id:
                            payload.setdefault('diagnostic_persistence', 'run_log')
                            payload.setdefault('evidence', [])
                            return payload
                    except (OSError, ValueError, TypeError):
                        continue
        return None
    record = dict(row)
    path = Path(record['diagnostic_path']).resolve() if record.get('diagnostic_path') else None
    if path and path.is_file() and path.is_relative_to(runtime.logs.resolve()):
        try:
            payload = read_json(path)
            if payload.get('error_id') == record['error_id']:
                record.update(payload)
        except (OSError, ValueError, TypeError):
            pass
    record.setdefault('exception_type', 'Exception')
    record.setdefault('diagnostic_persistence', 'db_only')
    record.setdefault('evidence', [])
    return record


def doctor(workspace):
    """Read-only checks: never initialize, install or download anything."""
    root = workspace.root
    runtime = workspace.runtime
    checks = []
    supported = (3, 11) <= sys.version_info[:2] < (3, 14)
    checks.append({'name': 'python', 'status': 'ok' if supported else 'error',
                   'message': f'Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}',
                   'next_action': None if supported else '使用啟動器準備 Python 3.11–3.13。'})
    uv = shutil.which('uv')
    checks.append({'name': 'uv', 'status': 'ok' if uv else 'warning',
                   'message': '已找到 uv。' if uv else '尚未找到 uv。',
                   'next_action': None if uv else '使用隨附啟動器準備 uv。'})
    readable = root.is_dir() and os.access(root, os.R_OK)
    writable = root.is_dir() and os.access(root, os.W_OK)
    checks.append({'name': 'workspace_permissions', 'status': 'ok' if readable and writable else 'error',
                   'message': '工作區可讀寫。' if readable and writable else '工作區不存在或權限不足。',
                   'next_action': None if readable and writable else '選擇可讀寫的本機工作區。'})
    database = runtime.home / 'state.sqlite3'
    if not database.is_file():
        checks.append({'name': 'sqlite', 'status': 'warning', 'message': '狀態資料庫尚未建立。',
                       'next_action': '首次執行流程時由底座建立；此檢查不會建立資料庫。'})
    else:
        try:
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True, timeout=1)) as db:
                integrity = db.execute('PRAGMA quick_check').fetchone()[0]
                version = db.execute('PRAGMA user_version').fetchone()[0]
            from ..storage.migrations import MIGRATIONS
            healthy = integrity == 'ok' and 1 <= version <= MIGRATIONS[-1].VERSION
            legacy = integrity == 'ok' and version == 0
            wal = database.with_name(database.name + '-wal')
            wal_pending = wal.is_file() and wal.stat().st_size > 0
            checks.append({'name': 'sqlite', 'status': 'warning' if wal_pending else ('ok' if healthy else ('warning' if legacy else 'error')),
                           'message': f'資料庫完整性：{integrity}；schema 版本：{version}。',
                           'next_action': '仍有 WAL 寫入；待目前工作結束後重新檢查。' if wal_pending else
                           (None if healthy else ('依 migration 指引先備份再升級。' if legacy else '停止寫入，依 migration 備份指引查核。'))})
        except sqlite3.Error:
            checks.append({'name': 'sqlite', 'status': 'error', 'message': '無法唯讀檢查資料庫。',
                           'next_action': '確認資料庫權限與磁碟狀態，保留原檔。'})
    resources = runtime.home / 'resources'
    try:
        if resources.is_symlink() or (hasattr(resources, 'is_junction') and resources.is_junction()):
            raise OSError('resource cache is a link')
        count = sum(1 for item in resources.iterdir() if item.is_dir()) if resources.is_dir() else 0
        checks.append({'name': 'resources', 'status': 'unknown',
                       'message': f'本機快取資源目錄：{count} 個；尚未逐檔校驗。',
                       'next_action': '選定流程後執行 verify 進行完整資源驗證。'})
    except OSError:
        checks.append({'name': 'resources', 'status': 'warning', 'message': '無法讀取資源快取目錄。',
                       'next_action': '檢查 share_states/resources 權限。'})
    return {'status': 'needs_attention' if any(item['status'] in {'error','warning','unknown'} for item in checks) else 'ready',
            'checks': checks}


def diagnose(runtime, run_id):
    legacy, execution = _run(runtime, run_id)
    logdir = _logdir(runtime, legacy['flow_id'], run_id)
    with closing(runtime.connect()) as db:
        attempts = [dict(row) for row in db.execute('SELECT attempt_id,step_id,attempt_no,status,error_id FROM step_attempts WHERE run_id=? ORDER BY step_id,attempt_no', (run_id,))]
    timeline = runtime.events(run_id, limit=100_000)['events']
    latest = timeline[-1] if timeline else None
    confirmed_names = {'run.created', 'run.claimed', 'resource.verified', 'step.finished',
                       'step.cache_hit', 'process.spawned', 'delivery.verified', 'delivery.published'}
    confirmed = next((item for item in reversed(timeline)
                      if item['name'] in confirmed_names or
                      (item['name'] == 'process.exited' and item.get('attributes', {}).get('status') == 'succeeded')), None)
    diagnostic = _diagnostic(runtime, run_id=run_id)
    code = diagnostic['code'] if diagnostic else legacy.get('error')
    explanation = explain_error(code, cause_status=diagnostic.get('cause_status', 'unknown') if diagnostic else 'unknown',
                                evidence=safe_export(diagnostic)['evidence'] if diagnostic else None) if code else None
    return {'run_id': run_id, 'flow_id': legacy['flow_id'], 'job_id': legacy['job_id'],
            'legacy_status': legacy['status'],
            'execution_status': execution['execution_status'] if execution else 'legacy_unknown',
            'result_status': execution['result_status'] if execution else None,
            'delivery_status': execution['delivery_status'] if execution else None,
            'step_attempts': attempts,
            'last_event': {'name': latest['name'], 'timestamp_utc': latest['timestamp_utc']} if latest else None,
            'last_confirmed_event': {'name': confirmed['name'], 'timestamp_utc': confirmed['timestamp_utc']} if confirmed else None,
            'error': explanation, 'error_id': diagnostic['error_id'] if diagnostic else None,
            'diagnostic': safe_export(diagnostic) if diagnostic else None,
            'log_dir': str(logdir)}


def logs(runtime, run_id, *, tail=0):
    legacy, _ = _run(runtime, run_id)
    if not 0 <= tail <= 100:
        raise FlowError('INVALID_CONFIGURATION', '--tail 必須介於 0 與 100')
    logdir = _logdir(runtime, legacy['flow_id'], run_id)
    files = []
    if logdir.is_dir():
        for current, folders, names in os.walk(logdir, followlinks=False):
            folders[:] = [name for name in folders if not (Path(current) / name).is_symlink() and
                          not (hasattr(Path(current) / name, 'is_junction') and (Path(current) / name).is_junction()) and
                          (Path(current) / name).resolve().is_relative_to(logdir)]
            for name in names:
                path = Path(current) / name
                if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(logdir):
                    files.append({'name': path.relative_to(logdir).as_posix(), 'path': str(path),
                                  'size_bytes': path.stat().st_size})
    files.sort(key=lambda item: item['name'])
    result = {'run_id': run_id, 'files': files}
    if tail:
        from ..diagnostics import redact
        console = logdir / 'console.log'
        if console.is_file() and not console.is_symlink() and console.resolve().is_relative_to(logdir):
            result['console_tail'] = [redact(line) for line in console.read_text(encoding='utf-8', errors='replace').splitlines()[-tail:]]
    return result


def explain_error_id(runtime, identifier):
    if identifier in STANDARD or identifier in ALIASES:
        return {'error_id': None, 'explanation': explain_error(identifier), 'diagnostic': None}
    if not ERROR_ID.fullmatch(identifier):
        raise FlowError('DIAGNOSTIC_UNKNOWN', '請提供有效的 error_id 或已知錯誤碼')
    record = _diagnostic(runtime, error_id=identifier)
    if record is None:
        raise FlowError('DIAGNOSTIC_UNKNOWN', '找不到指定錯誤編號', error_id=identifier)
    return {'error_id': identifier,
            'explanation': explain_error(record['code'], cause_status=record.get('cause_status', 'unknown'),
                                         evidence=safe_export(record)['evidence']),
            'diagnostic': safe_export(record)}


def export_preview(runtime, run_id, *, destination=None, confirm=False):
    _run(runtime, run_id)
    record = _diagnostic(runtime, run_id=run_id)
    if record is None:
        raise FlowError('DIAGNOSTIC_UNKNOWN', '此 Run 尚無可匯出的錯誤診斷', run_id=run_id)
    preview = {'schema_version': '1.0', 'run_id': run_id, 'diagnostic': safe_export(record)}
    if not confirm:
        return {'status': 'preview', 'preview': preview,
                'notice': '請檢視預覽後，以 --confirm 與 --output 明確指定本機匯出位置。'}
    if destination is None:
        raise FlowError('INVALID_CONFIGURATION', '確認匯出時必須提供 --output')
    target = Path(destination).expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise FlowError('OUTPUT_EXISTS', '匯出目標已存在，請另選新檔案')
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.diagnostic-', suffix='.json', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(preview, output, ensure_ascii=False, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        try:
            if os.name == 'nt':
                os.rename(temporary, target)  # Windows fails if target already exists
            else:
                os.link(temporary, target)  # atomic create without replacement
        except OSError as link_error:
            if link_error.errno not in {errno.EPERM, errno.EACCES, errno.ENOTSUP, errno.EOPNOTSUPP}:
                raise
            # Some Windows sandboxes forbid hard links. Reserve exclusively, then
            # atomically replace only the incomplete marker created by this call.
            with target.open('x', encoding='utf-8') as reservation:
                reservation.write('{"status":"incomplete"}\n')
                reservation.flush()
                os.fsync(reservation.fileno())
            os.replace(temporary, target)
    except FileExistsError as exc:
        raise FlowError('OUTPUT_EXISTS', '匯出目標已存在，請另選新檔案') from exc
    except OSError as exc:
        raise FlowError('DIAGNOSTIC_PERSIST_FAILED', '本機匯出失敗，未建立正式診斷檔') from exc
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {'status': 'exported', 'path': str(target), 'preview': preview}


def human_text(command, result):
    statuses = {'ready': '已就緒', 'needs_attention': '需處理', 'ok': '正常',
                'warning': '提醒', 'error': '錯誤', 'unknown': '尚未確認',
                'confirmed': '已確認', 'suspected': '可能', 'failed': '失敗',
                'interrupted': '中斷', 'cancelled': '已取消', 'succeeded': '成功'}
    if command == 'doctor':
        return '\n'.join([f'環境狀態：{statuses.get(result["status"], result["status"])}', *[
            f'[{statuses.get(item["status"], item["status"])}] {item["name"]}：{item["message"]}' +
            (f' 下一步：{item["next_action"]}' if item['next_action'] else '') for item in result['checks']]])
    if command == 'logs':
        return '\n'.join([f'Run：{result["run_id"]}', *[
            f'{item["name"]}（{item["size_bytes"]} bytes）：{item["path"]}' for item in result['files']],
            *result.get('console_tail', [])])
    if command == 'export-diagnostics':
        if result['status'] == 'preview':
            return '診斷預覽：' + json.dumps(result['preview'], ensure_ascii=False, indent=2) + '\n' + result['notice']
        return '已匯出已遮罩診斷：' + result['path']
    explanation = result.get('explanation') or result.get('error')
    lines = [f'Run：{result["run_id"]}' if result.get('run_id') else f'錯誤編號：{result.get("error_id") or "依錯誤碼查詢"}']
    if result.get('execution_status'):
        lines.append('執行狀態：' + statuses.get(result['execution_status'], result['execution_status']))
    if result.get('last_event'):
        lines.append('最後事件：' + result['last_event']['name'])
    if result.get('last_confirmed_event'):
        lines.append('最後確認操作：' + result['last_confirmed_event']['name'])
    if explanation:
        lines.extend([f'問題：{explanation["user_title"]}（{explanation["code"]}）',
                      explanation['user_message'], '確定程度：' + statuses.get(explanation['cause_status'], explanation['cause_status']),
                      *['下一步：' + action for action in explanation['suggested_actions']]])
    return '\n'.join(lines)
