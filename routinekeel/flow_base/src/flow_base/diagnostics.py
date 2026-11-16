"""Structured exception evidence without validation payloads or frame locals."""
from __future__ import annotations
import json
import math
import os
import re
import traceback
import uuid
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


def record_exception(exc, folder, **context):
    previous = getattr(exc,'_flow_diagnostic',{})
    record = {**previous, **describe_exception(exc), **redact(context)}
    record.setdefault('error_id','error_'+uuid.uuid4().hex)
    record['causes'] = []
    cause = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    seen = {id(exc)}
    while cause is not None and id(cause) not in seen and len(record['causes']) < 8:
        seen.add(id(cause));record['causes'].append(describe_exception(cause))
        cause = cause.__cause__ or (None if cause.__suppress_context__ else cause.__context__)
    folder = Path(folder)
    record['diagnostic_json'] = str(folder/'error.json')
    record['exception_log'] = str(folder/'exception.log')
    lines = [record['error_id'], record['exception_type']+': '+record['message']]
    for item in [record,*record['causes']]:
        if item is not record:
            lines.append('Caused by '+item['exception_type']+': '+item['message'])
        for frame in item['stack']:
            lines.append(f'  File "{frame["file"]}", line {frame["line"]}, in {frame["function"]}')
        if item.get('validation_errors'):
            lines.append(json.dumps(item['validation_errors'],ensure_ascii=False,indent=2))
    try:
        atomic_json(folder/'error.json',record)
        atomic_bytes(folder/'exception.log', ('\n'.join(lines)+'\n').encode())
    except OSError as write_error:
        record['diagnostic_write_error'] = type(write_error).__name__
        try:
            from .core.run_context import current
            context = current()
            if context and context.event_sink:
                context.event_sink.record('diagnostic.persist_failed', severity='ERROR',
                                          attributes={'code': type(write_error).__name__})
        except Exception:
            pass
    try:
        exc._flow_diagnostic = record
    except (AttributeError,TypeError):
        pass
    return record


def cli_error(runtime, exc, code=None):
    prior = getattr(exc,'_flow_diagnostic',{})
    error_id = prior.get('error_id','error_'+uuid.uuid4().hex)
    folder = runtime.logs/'launcher'/launcher_id(runtime)/error_id
    diagnostic = record_exception(exc,folder,launch_id=launcher_id(runtime),error_id=error_id)
    if prior.get('run_diagnostic_json') or (prior.get('run_id') and prior.get('diagnostic_json')):
        diagnostic['run_diagnostic_json'] = prior.get('run_diagnostic_json', prior['diagnostic_json'])
        # The returned link is also persisted for an operator reading the file later.
        try:atomic_json(folder/'error.json',diagnostic)
        except OSError:pass
    error = {'code':code or diagnostic['code'], 'message':diagnostic['message'],
             'details':diagnostic.get('details',{}), 'diagnostic':diagnostic}
    return {'ok':False,'error':error}
