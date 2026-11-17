"""Structured exception evidence without validation payloads or frame locals."""
from __future__ import annotations
import json
import math
import os
import re
import traceback
import uuid
import sys
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from pydantic import ValidationError
from .runtime import FlowError, atomic_json, atomic_bytes


def redact(value):
    if isinstance(value, dict):
        return {str(k): '[REDACTED]' if re.search(r'(?i)(password|secret|api[_-]?key|authorization|access[_-]?token)', str(k))
                or str(k).lower() in {'prompt','messages','transcript','input','text'} else redact(v)
                for k,v in value.items()}
    if isinstance(value, (list,tuple)):
        return [redact(v) for v in value]
    if not isinstance(value, str):
        if isinstance(value,float) and not math.isfinite(value):
            return str(value)
        return value if value is None or isinstance(value,(int,float,bool)) else type(value).__name__
    result = value
    for name, secret in os.environ.items():
        if len(secret) >= 8 and re.search(r'(?i)(api.?key|secret|password|access.?token|auth.?token)',name):
            result = result.replace(secret, '[REDACTED]')
    result = re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9._~+/=-]+', r'\1[REDACTED]', result)
    result = re.sub(r'(?i)\b(api[_-]?key|password|secret|access[_-]?token)\s*[:=]\s*\S+',
                    r'\1=[REDACTED]', result)
    return result[:16000]


def describe_exception(exc):
    code = exc.code if isinstance(exc, FlowError) else type(exc).__name__
    record = {'code': code, 'exception_type': type(exc).__name__}
    if isinstance(exc, ValidationError):
        issues = []
        for error in exc.errors(include_url=False, include_context=False):
            item = {'type': error['type'], 'location': list(error['loc']), 'message': error['msg']}
            source = error.get('input')
            if isinstance(source, dict) and isinstance(source.get('id'), (str,int)):
                item['record_id'] = source['id']
            issues.append(item)
        record['validation_errors'] = issues
        record['message'] = '; '.join(
            ('record_id='+str(item['record_id'])+' | ' if 'record_id' in item else '') + item['message']
            for item in issues) or 'Validation failed'
    else:
        record['message'] = str(exc) or ('Execution interrupted' if isinstance(exc,KeyboardInterrupt) else type(exc).__name__)
    if isinstance(exc,FlowError):
        record['details'] = exc.details
    record['stack'] = [{'file':frame.filename,'line':frame.lineno,'function':frame.name}
                       for frame in traceback.extract_tb(exc.__traceback__)]
    return redact(record)


def launcher_id(runtime):
    existing = getattr(runtime,'diagnostic_launch_id',None)
    if existing:
        return existing
    supplied = os.environ.get('FLOW_BASE_LAUNCH_ID','')
    identifier = supplied if re.fullmatch(r'launch_[a-f0-9]{32}',supplied) else 'launch_'+uuid.uuid4().hex
    runtime.diagnostic_launch_id = identifier
    return identifier


def record_exception(exc, folder, *, runtime=None, parent_error_id=None, **context):
    """Best-effort evidence. Every failure mode returns a minimal primary record."""
    from .observability.diagnostics import cause_evidence

    try:
        previous = getattr(exc, '_flow_diagnostic', {})
        if not isinstance(previous, dict):
            previous = {}
    except BaseException:
        previous = {}
    error_id = previous.get('error_id') or context.get('error_id') or 'error_' + uuid.uuid4().hex
    code = exc.code if isinstance(exc, FlowError) else type(exc).__name__
    try:
        described = describe_exception(exc)
    except BaseException:
        described = {'code': code, 'exception_type': type(exc).__name__,
                     'message': 'Exception details unavailable', 'stack': []}
    try:
        safe_context = redact(context)
    except BaseException:
        safe_context = {}
    record = {**previous, **described, **safe_context, 'error_id': error_id}
    if parent_error_id:
        record['parent_error_id'] = parent_error_id
    try:
        record['cause_status'], record['evidence'] = cause_evidence(exc)
    except BaseException:
        record['cause_status'], record['evidence'] = 'unknown', []
    record['causes'] = []
    try:
        cause = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
        seen = {id(exc)}
        while cause is not None and id(cause) not in seen and len(record['causes']) < 8:
            seen.add(id(cause))
            try:
                record['causes'].append(describe_exception(cause))
            except BaseException:
                record['causes'].append({'code': type(cause).__name__, 'exception_type': type(cause).__name__,
                                         'message': 'Cause details unavailable', 'stack': []})
            cause = cause.__cause__ or (None if cause.__suppress_context__ else cause.__context__)
    except BaseException:
        pass
    folder = Path(folder)
    record['diagnostic_json'] = str(folder / 'error.json')
    record['exception_log'] = str(folder / 'exception.log')
    lines = [error_id, record['exception_type'] + ': ' + record['message']]
    for item in [record, *record['causes']]:
        if item is not record:
            lines.append('Caused by ' + item['exception_type'] + ': ' + item['message'])
        for frame in item.get('stack', []):
            lines.append(f'  File "{frame["file"]}", line {frame["line"]}, in {frame["function"]}')
        if item.get('validation_errors'):
            try:
                lines.append(json.dumps(item['validation_errors'], ensure_ascii=False, indent=2))
            except BaseException:
                pass

    record['diagnostic_persistence'] = 'run_log'
    json_saved = False
    text_saved = False
    for path, write in ((folder / 'error.json', lambda: atomic_json(folder / 'error.json', record)),
                        (folder / 'exception.log', lambda: atomic_bytes(folder / 'exception.log', ('\n'.join(lines)+'\n').encode()))):
        try:
            write()
            if path.name == 'error.json':
                json_saved = True
            else:
                text_saved = True
        except BaseException as write_error:
            record['diagnostic_write_error'] = type(write_error).__name__
            try:
                from .core.run_context import current
                active = current()
                if active and active.event_sink:
                    active.event_sink.record('diagnostic.persist_failed', severity='ERROR',
                                             attributes={'code': type(write_error).__name__})
            except BaseException:
                pass

    if runtime is None:
        try:
            from .core.run_context import current
            active = current()
            runtime = active.runtime if active else None
        except BaseException:
            runtime = None
    db_saved = False
    if runtime is not None:
        try:
            database = runtime.home / 'state.sqlite3'
            if database.is_file():
                with closing(runtime.connect()) as db:
                    with db:
                        db.execute('PRAGMA busy_timeout=250')
                        db.execute('INSERT OR IGNORE INTO diagnostics(error_id,run_id,step_attempt_id,code,cause_status,diagnostic_path,occurred_at) VALUES(?,?,?,?,?,?,?)',
                                   (error_id, record.get('run_id'), record.get('step_attempt_id'),
                                    code, record['cause_status'], record['diagnostic_json'] if json_saved else None,
                                    datetime.now(timezone.utc).isoformat()))
                db_saved = True
        except BaseException as db_error:
            record['diagnostic_db_error'] = type(db_error).__name__
    if not json_saved:
        record['diagnostic_json'] = None
        record['diagnostic_persistence'] = 'run_log' if text_saved else ('db_only' if db_saved else 'stderr_only')
    if not text_saved:
        record['exception_log'] = None
    if not json_saved and not text_saved and not db_saved:
        try:
            print(f'[{error_id}] {code}: 診斷儲存失敗；原始錯誤仍保留。', file=sys.stderr, flush=True)
        except BaseException:
            pass
    try:
        exc._flow_diagnostic = record
    except BaseException:
        pass
    return record


def cli_error(runtime, exc, code=None):
    prior = getattr(exc,'_flow_diagnostic',{})
    error_id = prior.get('error_id','error_'+uuid.uuid4().hex)
    folder = runtime.logs/'launcher'/launcher_id(runtime)/error_id
    diagnostic = record_exception(exc,folder,runtime=runtime,launch_id=launcher_id(runtime),error_id=error_id)
    if prior.get('run_diagnostic_json') or (prior.get('run_id') and prior.get('diagnostic_json') and prior.get('diagnostic_persistence') == 'run_log'):
        diagnostic['run_diagnostic_json'] = prior.get('run_diagnostic_json') or prior.get('diagnostic_json')
        # The returned link is also persisted for an operator reading the file later.
        try:atomic_json(folder/'error.json',diagnostic)
        except BaseException:pass
    error = {'code':code or diagnostic['code'], 'message':diagnostic['message'],
             'details':diagnostic.get('details',{}), 'diagnostic':diagnostic}
    try:
        from .observability.errors import explain_exception
        error['user_explanation'] = explain_exception(exc, diagnostic)
    except BaseException:
        pass
    return {'ok':False,'error':error}
