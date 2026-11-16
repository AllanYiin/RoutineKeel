"""Cause confidence, safe sharing, and fail-safe secondary reporting."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from pydantic import ValidationError

CONFIRMED_CODES = frozenset({'INPUT_MISSING', 'OUTPUT_EXISTS', 'OUTPUT_CONFLICT',
                             'STATE_CONFLICT', 'RESOURCE_HASH', 'PROCESS_TIMEOUT',
                             'PROCESS_START', 'OFFLINE_RESOURCE_MISSING'})
PUBLIC_CODES = CONFIRMED_CODES | frozenset({'PROCESS_FAILED', 'UNEXPECTED_ERROR',
    'INPUT_OR_ENVIRONMENT', 'INTERRUPTED', 'TASK_UNKNOWN', 'PREFLIGHT_BLOCKED',
    'DELIVERY_FAILED', 'PERMISSION_DENIED', 'SCHEMA_MISMATCH', 'SCHEMA_TOO_NEW',
    'BUSY', 'RUN_CANCEL_REQUESTED', 'PROCESS_CANCELLED', 'WORKER_CRASHED',
    'DIAGNOSTIC_PERSIST_FAILED'})
ERROR_ID = re.compile(r'^error_[a-f0-9]{32}$')
EXCEPTION_TYPES = frozenset({'FlowError', 'ValidationError', 'ValueError', 'KeyError',
    'TypeError', 'RuntimeError', 'OSError', 'FileNotFoundError', 'PermissionError',
    'OperationalError', 'TimeoutError', 'KeyboardInterrupt', 'Exception'})
EVIDENCE_KINDS = frozenset({'validation_error', 'runtime_check', 'process_exit', 'os_error'})


def cause_evidence(exc):
    """Classify only direct facts; nonzero exit never proves OOM or a model fault."""
    from ..runtime import FlowError

    if isinstance(exc, ValidationError):
        return 'confirmed', [{'kind': 'validation_error', 'count': len(exc.errors(include_url=False, include_context=False))}]
    if isinstance(exc, FlowError):
        if exc.code in CONFIRMED_CODES:
            return 'confirmed', [{'kind': 'runtime_check', 'code': exc.code}]
        if exc.code == 'PROCESS_FAILED':
            return 'suspected', [{'kind': 'process_exit', 'exit_code': exc.details.get('exit_code')}]
        return 'unknown', []
    if isinstance(exc, OSError) and isinstance(exc.errno, int):
        return 'suspected', [{'kind': 'os_error', 'errno': exc.errno}]
    return 'unknown', []


def safe_export(record):
    """Default shareable shape excludes messages, paths, prompts, locals and raw stacks."""
    def error_id(value):
        value = str(value or '')
        return value if ERROR_ID.fullmatch(value) else 'unavailable'

    def public_code(value):
        return value if value in PUBLIC_CODES else 'UNCLASSIFIED_ERROR'

    evidence = []
    for item in record.get('evidence', []):
        if not isinstance(item, dict):
            continue
        if item.get('kind') not in EVIDENCE_KINDS:
            continue
        safe = {'kind': item['kind']}
        if item.get('code') in PUBLIC_CODES:
            safe['code'] = item['code']
        for key in ('count', 'errno', 'exit_code'):
            if type(item.get(key)) is int:
                safe[key] = item[key]
        evidence.append(safe)
    exception_type = record.get('exception_type')
    return {'schema_version': '1.0', 'error_id': error_id(record.get('error_id')),
            'code': public_code(record.get('code')),
            'exception_type': exception_type if exception_type in EXCEPTION_TYPES else 'Exception',
            'cause_status': record.get('cause_status') if record.get('cause_status') in {'confirmed','suspected','unknown'} else 'unknown',
            'evidence': evidence, 'parent_error_id': error_id(record.get('parent_error_id')) if record.get('parent_error_id') else None,
            'diagnostic_persistence': record.get('diagnostic_persistence') if record.get('diagnostic_persistence') in {'run_log','db_only','stderr_only'} else 'stderr_only'}


def export_diagnostics(record, destination):
    """Write a local, shareable JSON. Callers choose the destination explicitly."""
    from ..runtime import atomic_json
    target = Path(destination)
    atomic_json(target, safe_export(record))
    return target


def guard_secondary(original, primary_record, operation, work, *, runtime=None):
    """Keep the original exception as the one that escapes a reporting boundary."""
    try:
        return work()
    except BaseException as secondary:
        entry = {'operation': str(operation)[:80], 'type': type(secondary).__name__}
        primary_record.setdefault('secondary_errors', []).append(entry)
        try:
            from ..diagnostics import record_exception
            primary_path = primary_record.get('diagnostic_json')
            base = Path(primary_path).parent if primary_path else (runtime.logs if runtime is not None else Path.cwd())
            folder = base / 'secondary' / ('secondary_' + uuid.uuid4().hex)
            result = record_exception(secondary, folder, runtime=runtime,
                                      parent_error_id=primary_record['error_id'], operation=operation)
            entry['error_id'] = result['error_id']
        except BaseException:
            pass
        return None
