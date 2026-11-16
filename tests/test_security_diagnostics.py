"""Integrated secret and path boundary for shareable diagnostics."""
import json

import pytest

from flow_base.core.service import WorkflowService
from flow_base.diagnostics import record_exception
from flow_base.runtime import FlowError
from flow_base.workspace import Workspace


def test_share_package_excludes_four_private_marker_classes(tmp_path):
    workspace = Workspace(tmp_path)
    flow_id = workspace.runtime.register_flow('fixture', '1', 'fixture', 'Fixture')
    run_id = workspace.runtime.start_run(flow_id, tmp_path / 'input', tmp_path / 'result')
    markers = ['RK_API_KEY_PRIVATE_123', 'Authorization: Bearer PRIVATE_456',
               'PROMPT_PRIVATE_BODY_789', 'DOCUMENT_CONTENT_PRIVATE_ABC']
    original = FlowError('TASK_PREPARE', ' | '.join(markers))
    diagnostic = record_exception(original, workspace.runtime.logs / flow_id / run_id,
                                  runtime=workspace.runtime, run_id=run_id, flow_id=flow_id)
    assert diagnostic['error_id'].startswith('error_')
    service = WorkflowService(tmp_path)
    outside = tmp_path.parent / 'shared-diagnostics.json'
    with pytest.raises(FlowError) as forbidden:
        service.export_diagnostics(run_id, outside)
    assert forbidden.value.code == 'PERMISSION_DENIED'
    target = tmp_path / 'shared-diagnostics.json'
    result = service.export_diagnostics(run_id, target)
    exported = target.read_text(encoding='utf-8')
    for marker in markers:
        assert marker not in exported
    data = json.loads(exported)
    assert data['diagnostic']['error_id'] == diagnostic['error_id']
    assert data['diagnostic']['code'] == 'TASK_PREPARE'
    assert result['path'] == str(target)
