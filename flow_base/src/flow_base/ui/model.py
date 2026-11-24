"""State and validation for the local UI; no Tk or workflow engine logic."""
from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..core.service import WorkflowService
from ..core.types import PlanRequest, StartRequest
from ..observability.errors import explain_exception
from ..runtime import FlowError

TEXT_EXTENSIONS = frozenset({'.txt', '.json', '.md', '.csv', '.srt', '.vtt', '.log'})
IMAGE_EXTENSIONS = frozenset({'.png', '.gif'})


@dataclass(frozen=True)
class Preview:
    path: str
    name: str
    size_bytes: int
    extension: str
    kind: str
    text: str | None = None
    reason: str | None = None


def fit_dimensions(width: int, height: int, limit_width: int, limit_height: int) -> tuple[int, int]:
    if min(width, height, limit_width, limit_height) <= 0:
        raise ValueError('預覽尺寸必須大於零')
    factor = min(1.0, limit_width / width, limit_height / height)
    return max(1, math.floor(width * factor)), max(1, math.floor(height * factor))


def preview_file(path: Path | str) -> Preview:
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise FlowError('INPUT_NOT_FOUND', '找不到輸入檔案')
    extension = target.suffix.lower()
    size = target.stat().st_size
    fields = {'path': str(target), 'name': target.name, 'size_bytes': size, 'extension': extension}
    if extension in IMAGE_EXTENSIONS:
        return Preview(**fields, kind='image')
    if extension in TEXT_EXTENSIONS:
        try:
            with target.open('rb') as stream:
                raw = stream.read(8193)
            if b'\x00' in raw:
                raise UnicodeError('binary content')
            text = raw[:8192].decode('utf-8-sig')
        except (UnicodeError, OSError):
            return Preview(**fields, kind='unsupported', reason='文字編碼或內容無法安全預覽')
        if len(raw) > 8192:
            text += '\n…（僅顯示前 8192 bytes）'
        return Preview(**fields, kind='text', text=text)
    return Preview(**fields, kind='unsupported', reason='目前介面不支援此格式的內容預覽')


def parse_overrides(text: str) -> dict:
    if not text.strip():
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise FlowError('INVALID_CONFIGURATION', '設定必須是有效的 JSON object') from exc
    if not isinstance(value, dict):
        raise FlowError('INVALID_CONFIGURATION', '設定必須是 JSON object')
    return value


def error_card(diagnostic: dict | None = None, *, exception: Exception | None = None) -> dict:
    if exception is not None:
        explanation = explain_exception(exception)
        return {'title': explanation['user_title'], 'stage': '預先檢查',
                'cause_status': explanation['cause_status'],
                'message': explanation['user_message'],
                'next_action': (explanation.get('suggested_actions') or ['請檢查設定。'])[0],
                'error_id': getattr(exception, 'error_id', None)}
    diagnostic = diagnostic or {}
    explanation = diagnostic.get('error') or {}
    last = diagnostic.get('last_event') or {}
    attempts = diagnostic.get('step_attempts') or []
    failed = next((row.get('step_id') for row in reversed(attempts)
                   if row.get('status') in {'failed', 'interrupted', 'cancelled'}), None)
    return {'title': explanation.get('user_title') or '執行未完成',
            'stage': failed or last.get('name') or '尚無階段資訊',
            'cause_status': explanation.get('cause_status') or 'unknown',
            'message': explanation.get('user_message') or '目前證據不足，請查看最後事件。',
            'next_action': (explanation.get('suggested_actions') or ['記下 error_id 並匯出已遮罩診斷。'])[0],
            'error_id': diagnostic.get('error_id')}


class LocalUIModel:
    def __init__(self, project_path):
        self.service = WorkflowService(project_path)
        self.project_path = self.service.workspace.root
        self.request = None
        self.plan_result = None
        self.run_id = None
        self.idempotency_key = None

    def open_project(self, path):
        result = self.service.open_project(path)
        self.project_path = self.service.workspace.root
        self.request = self.plan_result = self.run_id = self.idempotency_key = None
        return result

    def workflows(self):
        return self.service.list_workflows()

    def history(self, task=None):
        return self.service.history(task)

    def plan(self, task, source, output=None, overrides_text='', offline=False):
        overrides = parse_overrides(overrides_text)
        source = Path(source).expanduser().resolve() if source else None
        output = Path(output).expanduser().resolve() if output else None
        authorized = [path for path in (source, output) if path is not None]
        self.service.authorized_external_paths = frozenset(authorized)
        request = PlanRequest(task=task, source=source, output=output, overrides=overrides,
                              offline=offline, allow_external_paths=True)
        result = self.service.plan_workflow(request)
        self.request, self.plan_result = request, result
        self.idempotency_key = uuid.uuid4().hex
        return result

    def start(self):
        if self.request is None or self.plan_result is None:
            raise FlowError('PREFLIGHT_BLOCKED', '請先檢查計畫')
        if self.plan_result.plan.get('blocked'):
            raise FlowError('PREFLIGHT_BLOCKED', '請先排除計畫中的阻礙')
        request = StartRequest(**self.request.model_dump(),
                               idempotency_key=self.idempotency_key,
                               expected_spec_hash=self.plan_result.execution_spec_hash)
        result = self.service.start_workflow(request)
        self.run_id = result.run_id
        return result

    def status(self, run_id=None):
        return self.service.get_run_status(run_id or self.run_id)

    def cancel(self, run_id=None):
        return self.service.cancel_run(run_id or self.run_id)

    def diagnostics(self, run_id=None):
        return self.service.diagnose_run(run_id or self.run_id)

    def artifacts(self, run_id=None):
        return self.service.list_run_artifacts(run_id or self.run_id)

    def export_diagnostics(self, destination, run_id=None):
        target = Path(destination).expanduser().resolve()
        self.service.authorized_external_paths = frozenset(
            {*self.service.authorized_external_paths, target})
        return self.service.export_diagnostics(run_id or self.run_id, target)
