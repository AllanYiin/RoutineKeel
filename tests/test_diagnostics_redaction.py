"""Shareable diagnostics expose identifiers and evidence, not raw user data."""
import json

from flow_base.diagnostics import record_exception
from flow_base.observability.diagnostics import cause_evidence, safe_export, export_diagnostics
from flow_base.runtime import FlowError


def test_cause_confidence_requires_direct_evidence():
    assert cause_evidence(FlowError('INPUT_MISSING', 'missing'))[0] == 'confirmed'
    assert cause_evidence(FlowError('PROCESS_FAILED', 'exit', exit_code=137))[0] == 'suspected'
    assert cause_evidence(RuntimeError('unknown')) == ('unknown', [])


def test_safe_export_excludes_messages_paths_and_secret_markers(tmp_path):
    secret = 'PRIVATE_PROMPT_MARKER_123456'
    record = record_exception(FlowError('PROCESS_FAILED', 'prompt: ' + secret + ' Bearer abcdef123456',
                                        password=secret, path=r'C:\private\source.txt', exit_code=137),
                              tmp_path / 'internal', run_id='run_test')
    shared = safe_export(record)
    serialized = json.dumps(shared, ensure_ascii=False)
    for forbidden in (secret, 'abcdef123456', r'C:\private', 'prompt', 'password', 'stack', 'source.txt'):
        assert forbidden not in serialized
    assert shared['error_id'] == record['error_id']
    assert shared['cause_status'] == 'suspected'
    assert shared['evidence'] == [{'kind': 'process_exit', 'exit_code': 137}]
    assert 'message' not in shared and 'diagnostic_json' not in shared
    destination = export_diagnostics(record, tmp_path / 'share.json')
    assert json.loads(destination.read_text(encoding='utf-8')) == shared
    assert secret not in destination.read_text(encoding='utf-8')


def test_safe_export_rejects_untrusted_identifier_fields():
    marker = 'PRIVATE_SECRET_MARKER'
    shared = safe_export({'error_id': marker, 'code': marker,
                          'exception_type': marker, 'cause_status': 'confirmed',
                          'evidence': [{'kind': marker, 'code': marker}],
                          'parent_error_id': marker, 'diagnostic_persistence': 'run_log'})
    assert marker not in json.dumps(shared)
    assert shared['code'] == 'UNCLASSIFIED_ERROR'
