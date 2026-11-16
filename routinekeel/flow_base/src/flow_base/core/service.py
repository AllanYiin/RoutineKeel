"""Local WorkflowService shared by CLI and optional adapters; no HTTP server."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from ..runtime import FlowError, atomic_json, read_json
from ..workspace import Workspace, catalog
from .types import PlanRequest, PlanResult, StartRequest, StartResult, SubmitRequest, SubmitResult

RUN_ID = re.compile(r'^run_[a-f0-9]{32}$')


class WorkflowService:
    contract_version = '1.0'

    def __init__(self, project_path, *, authorized_external_paths=()):
        self.workspace = project_path if isinstance(project_path, Workspace) else Workspace(Path(project_path))
        def authorized(path):
            candidate = Path(path).expanduser()
            return (candidate if candidate.is_absolute() else self.workspace.root / candidate).resolve()
        self.authorized_external_paths = frozenset(authorized(path)
                                                    for path in authorized_external_paths if path is not None)

    def open_project(self, project_path):
        """Select a local workspace without creating state or loading task code."""
        self.workspace = Workspace(Path(project_path))
        self.authorized_external_paths = frozenset()
        tasks = self.workspace.discover(read_only=True)
        return {'contract_version': self.contract_version, 'project_path': str(self.workspace.root),
                'exists': self.workspace.root.is_dir(), 'workflow_count': len(tasks)}

    def list_workflows(self):
        return catalog(self.workspace)

    def describe_workflow(self, workflow_id):
        task = self.workspace.task(workflow_id)
        return {'contract_version': self.contract_version, 'flow_id': task.flow_id,
                'id': task.manifest.id, 'title': task.manifest.title,
                'version': task.manifest.version, 'description': task.manifest.description,
                'actions': list(task.manifest.actions), 'outputs': dict(task.manifest.outputs),
                'input_extensions': list(task.manifest.input_extensions)}

    def history(self, task=None, limit=30):
        flow_id = self.workspace.task(task).flow_id if task else None
        return self.workspace.runtime.history(flow_id, max(1, min(limit, 1000)))

    def prepare_workflow(self, task_id, *, overrides=None, offline=False):
        task = self.workspace.task(task_id)
        adapter = task.load(self.workspace.runtime)
        workflow = task.configuration(self.workspace.runtime, overrides or {})
        return adapter.prepare(self.workspace.runtime, workflow, offline)

    def verify_workflow(self, task_id, *, overrides=None):
        task = self.workspace.task(task_id)
        adapter = task.load(self.workspace.runtime)
        workflow = task.configuration(self.workspace.runtime, overrides or {})
        return adapter.verify(self.workspace.runtime, workflow)

    def launch_prepare(self, task_id, *, overrides=None, offline=False):
        """Prepare and verify one configured workflow for an interactive launcher."""
        task = self.workspace.task(task_id)
        adapter = task.load(self.workspace.runtime)
        workflow = task.configuration(self.workspace.runtime, overrides or {})
        description = list(adapter.describe(workflow)) if hasattr(adapter, 'describe') else []
        prepared = adapter.prepare(self.workspace.runtime, workflow, offline)
        verified = adapter.verify(self.workspace.runtime, workflow)
        if prepared.get('ok') is False or verified.get('ok') is False:
            raise FlowError('TASK_PREPARE', '流程環境驗證未通過', verification=verified)
        return {'description': description, 'prepared': prepared, 'verified': verified}

    def demo_workflow(self, task_id, output, *, overrides=None):
        from ..engine import run_demo
        task = self.workspace.task(task_id)
        adapter = task.load(self.workspace.runtime)
        task.configuration(self.workspace.runtime, overrides or {})
        if not hasattr(adapter, 'demo'):
            raise FlowError('TASK_CAPABILITY', '此流程沒有示範能力')
        return run_demo(self.workspace.runtime, task, self._path(output, allow_external=True))

    def invoke_action(self, task_id, name, parameters):
        if not isinstance(parameters, dict):
            raise FlowError('TASK_ACTION_PARAMETERS', '能力參數必須是 JSON object')
        task = self.workspace.task(task_id)
        return task.invoke_action(self.workspace.runtime, name, parameters)

    def transfer_cache(self, task_id, *, direction, source=None, output=None, resources=None, overrides=None):
        from ..resources import export_resources, import_resources
        task = self.workspace.task(task_id)
        adapter = task.load(self.workspace.runtime)
        task.configuration(self.workspace.runtime, overrides or {})
        if not hasattr(adapter, 'resources'):
            raise FlowError('TASK_CAPABILITY', '此流程沒有可攜資源')
        catalogue = adapter.resources()
        if direction == 'export':
            if output is None or not resources:
                raise FlowError('INVALID_CONFIGURATION', '匯出需要目標檔與資源名稱')
            return export_resources(self.workspace.runtime, self._path(output, allow_external=True),
                                    resources or [], catalogue)
        if direction == 'import':
            if source is None:
                raise FlowError('INVALID_CONFIGURATION', '匯入需要來源檔')
            return import_resources(self.workspace.runtime, self._path(source, allow_external=True), catalogue)
        raise FlowError('INVALID_CONFIGURATION', '未知的快取傳輸方向')

    def _path(self, value, *, allow_external):
        if value is None:
            return None
        path = Path(value).expanduser()
        path = (path if path.is_absolute() else self.workspace.root / path).resolve()
        if not path.is_relative_to(self.workspace.root) and not (
                allow_external and path in self.authorized_external_paths):
            raise FlowError('PERMISSION_DENIED', 'Service 未授權讀寫工作區以外的路徑')
        return path

    def plan_workflow(self, request):
        request = PlanRequest.model_validate(request)
        source = self._path(request.source, allow_external=request.allow_external_paths)
        output = self._path(request.output, allow_external=request.allow_external_paths)
        try:
            json.dumps(request.overrides, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise FlowError('INVALID_CONFIGURATION', '計畫參數必須是可序列化的 JSON') from exc
        payload = {'workspace': str(self.workspace.root), 'task': request.task,
                   'source': str(source) if source is not None else None,
                   'output': str(output) if output is not None else None,
                   'overrides': request.overrides, 'offline': request.offline}
        from ..execution.process import worker_environment
        with tempfile.TemporaryDirectory(prefix='routinekeel-service-plan-') as temporary:
            folder = Path(temporary)
            input_path, output_path = folder / 'request.json', folder / 'response.json'
            atomic_json(input_path, payload)
            env = worker_environment()
            env['FLOW_BASE_PLAN_READ_ONLY'] = '1'
            process = subprocess.run([sys.executable, '-m', 'flow_base.core.plan_probe',
                                      '--request', str(input_path), '--response', str(output_path)],
                                     cwd=folder, env=env, capture_output=True, timeout=40)
            if not output_path.is_file():
                raise FlowError('PLAN_PROBE_FAILED', '隔離計畫程序未回報結果', exit_code=process.returncode)
            response = read_json(output_path)
        if not response.get('ok'):
            error = response.get('error') or {}
            raise FlowError(error.get('code', 'PLAN_PROBE_FAILED'),
                            error.get('message', '計畫未能完成'), **(error.get('details') or {}))
        return PlanResult.model_validate(response['result'])

    def start_workflow(self, request):
        from ..execution.supervisor import start_detached
        from ..outputs import choose_output

        request = StartRequest.model_validate(request)
        planned = self.plan_workflow(PlanRequest(**request.model_dump(exclude={'idempotency_key', 'expected_spec_hash'})))
        if planned.plan.get('blocked'):
            raise FlowError('PREFLIGHT_BLOCKED', '請先處理計畫中的阻礙', blockers=planned.plan['blocked'])
        if planned.execution_spec_hash is None:
            raise FlowError('PREFLIGHT_BLOCKED', '缺少可驗證的輸入或執行規格')
        if request.expected_spec_hash and planned.execution_spec_hash != request.expected_spec_hash:
            raise FlowError('RUN_SPEC_MISMATCH', '輸入或設定已改變；請重新檢查計畫')
        task = self.workspace.task(request.task)
        source = self._path(request.source, allow_external=request.allow_external_paths)
        output = self._path(request.output, allow_external=request.allow_external_paths)
        resolved_output = choose_output(self.workspace.runtime, task, source, output)
        launched = start_detached(self.workspace, task, None, source, resolved_output,
                                  overrides=request.overrides, offline=request.offline,
                                  idempotency_key=request.idempotency_key,
                                  spec_hash=planned.execution_spec_hash)
        return StartResult(run_id=launched['run_id'], status=launched['status'],
                           submitted_at=datetime.now(timezone.utc).isoformat(),
                           execution_spec_hash=planned.execution_spec_hash)

    def submit_workflow(self, request):
        """Validate, persist and return an ID without launching code in this process."""
        from .queue import QueueStore

        request = SubmitRequest.model_validate(request)
        planned = self.plan_workflow(PlanRequest(**request.model_dump(
            exclude={'idempotency_key', 'expected_spec_hash'})))
        if planned.plan.get('blocked') or not planned.plan.get('ready_to_prepare'):
            raise FlowError('PREFLIGHT_BLOCKED', '請先處理計畫中的阻礙',
                            blockers=planned.plan.get('blocked') or [])
        if not planned.execution_spec_hash:
            raise FlowError('PREFLIGHT_BLOCKED', '缺少可驗證的執行規格')
        if request.expected_spec_hash and request.expected_spec_hash != planned.execution_spec_hash:
            raise FlowError('RUN_SPEC_MISMATCH', '輸入或設定已改變；請重新檢查計畫')
        source = self._path(request.source, allow_external=request.allow_external_paths)
        output = self._path(planned.plan.get('output'), allow_external=request.allow_external_paths)
        if source is None or not source.is_file() or output is None:
            raise FlowError('INPUT_NOT_FOUND', '請提供現有的輸入檔案與成果位置')
        if not source.is_relative_to(self.workspace.root) or not output.is_relative_to(self.workspace.root):
            raise FlowError('PERMISSION_DENIED', '跨程序佇列只接受工作區內檔案與成果路徑')
        row = QueueStore(self.workspace.runtime).submit(
            workflow_id=request.task, flow_id=planned.plan['flow_id'], source=source,
            output=output, overrides=request.overrides, offline=request.offline,
            spec_hash=planned.execution_spec_hash, idempotency_key=request.idempotency_key)
        return SubmitResult(execution_id=row['execution_id'], status=row['status'],
                            run_id=row['run_id'], submitted_at=row['submitted_at'],
                            execution_spec_hash=row['execution_spec_hash'],
                            poll_after_ms=None if row['status'] in {'succeeded','failed','interrupted','cancelled'} else 1000)

    def get_workflow_status(self, execution_id):
        from .queue import QueueStore, TERMINAL

        row = QueueStore(self.workspace.runtime).get(execution_id)
        status = row['status']
        run = None
        if row['run_id']:
            run = self.get_run_status(row['run_id'])
            if run['execution_status'] in TERMINAL:
                status = run['execution_status']
        progress = None
        current_step = None
        if row['run_id']:
            path = self.workspace.runtime.logs / row['flow_id'] / row['run_id'] / 'live_progress.json'
            try:
                live = read_json(path)
                current_step = live.get('step_id') if isinstance(live.get('step_id'), str) else None
                value = live.get('progress')
                if isinstance(value, dict):
                    progress = {key: value[key] for key in ('completed', 'total')
                                if type(value.get(key)) in (int, float) and value[key] >= 0}
            except (OSError, ValueError, TypeError):
                pass
        return {'contract_version': self.contract_version, 'execution_id': execution_id,
                'status': status, 'run_id': row['run_id'], 'current_step': current_step,
                'progress': progress, 'result_status': run['result_status'] if run else None,
                'error_id': run['error_id'] if run else None,
                'error_code': row['error_code'], 'submitted_at': row['submitted_at'],
                'started_at': row['started_at'], 'finished_at': row['finished_at'],
                'poll_after_ms': None if status in TERMINAL else 1000}

    def get_workflow_result(self, execution_id):
        status = self.get_workflow_status(execution_id)
        if status['status'] != 'succeeded':
            return {'execution_id': execution_id, 'status': status['status'],
                    'run_id': status['run_id'], 'result_available': False,
                    'result_status': status['result_status'], 'error_id': status['error_id'],
                    'error_code': status['error_code']}
        return {'execution_id': execution_id, 'status': 'succeeded',
                'run_id': status['run_id'], 'result_available': True,
                'result_status': status['result_status'],
                'artifacts': self.list_run_artifacts(status['run_id'])}

    def cancel_workflow(self, execution_id):
        from .queue import QueueStore

        row = QueueStore(self.workspace.runtime).cancel(execution_id)
        if row['run_id'] and row['status'] == 'cancel_requested':
            try:
                self.cancel_run(row['run_id'])
            except FlowError as exc:
                if exc.code != 'STATE_CONFLICT':
                    raise
        status = self.get_workflow_status(execution_id)
        return {'execution_id': execution_id, 'status': status['status'],
                'run_id': status['run_id'],
                'confirmed_stopped': status['status'] == 'cancelled'}

    def run_workflow_sync(self, request):
        from ..engine import run_workflow
        request = StartRequest.model_validate(request)
        source = self._path(request.source, allow_external=request.allow_external_paths)
        output = self._path(request.output, allow_external=request.allow_external_paths)
        task = self.workspace.task(request.task)
        workflow = task.configuration(self.workspace.runtime, request.overrides)
        return run_workflow(self.workspace.runtime, task, workflow, source, output, offline=request.offline)

    def _known_run(self, run_id):
        if not RUN_ID.fullmatch(run_id):
            raise FlowError('RUN_UNKNOWN', '請提供有效的 Run ID')
        database = self.workspace.runtime.home / 'state.sqlite3'
        if not database.is_file():
            raise FlowError('RUN_UNKNOWN', '找不到指定 Run', run_id=run_id)
        import sqlite3
        try:
            with closing(sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
                if db.execute('SELECT 1 FROM runs WHERE run_id=?', (run_id,)).fetchone():
                    return
            wal = database.with_name(database.name + '-wal')
            if wal.is_file() and wal.stat().st_size > 0:
                with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
                    if db.execute('SELECT 1 FROM runs WHERE run_id=?', (run_id,)).fetchone():
                        return
        except sqlite3.Error as exc:
            raise FlowError('SCHEMA_MISMATCH', '無法讀取現有 Run 資料') from exc
        raise FlowError('RUN_UNKNOWN', '找不到指定 Run', run_id=run_id)

    def get_run_status(self, run_id):
        from .lifecycle import RunStore
        self._known_run(run_id)
        self.workspace.runtime.initialize()
        execution = RunStore(self.workspace.runtime).get(run_id)
        with closing(self.workspace.runtime.connect()) as db:
            legacy = db.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone()
        return {'contract_version': self.contract_version, 'run_id': run_id,
                'execution_status': execution['execution_status'] if execution else 'legacy_unknown',
                'execution_spec_hash': execution['execution_spec_hash'] if execution else None,
                'result_status': execution['result_status'] if execution else None,
                'delivery_status': execution['delivery_status'] if execution else None,
                'state_version': execution['state_version'] if execution else None,
                'error_id': execution['error_id'] if execution else None,
                'legacy_status': legacy['status'] if legacy else None}

    def list_run_events(self, run_id, cursor=None, limit=100):
        if not 1 <= limit <= 1000:
            raise FlowError('INVALID_CONFIGURATION', '事件頁數必須介於 1 與 1000')
        self._known_run(run_id)
        return self.workspace.runtime.events(run_id, cursor=cursor, limit=limit)

    def cancel_run(self, run_id, reason='user'):
        from .lifecycle import RunStore
        if reason not in {'user', 'shutdown', 'superseded'}:
            raise FlowError('INVALID_CONFIGURATION', '取消原因不在允許清單')
        self._known_run(run_id)
        store = RunStore(self.workspace.runtime)
        store.request_cancel(run_id)
        state = store.get(run_id)
        return {'run_id': run_id, 'status': state['execution_status'],
                'confirmed_stopped': state['execution_status'] == 'cancelled'}

    def diagnose_run(self, run_id):
        from ..observability.commands import diagnose
        self._known_run(run_id)
        return diagnose(self.workspace.runtime, run_id)

    def list_run_artifacts(self, run_id):
        from .lifecycle import resolve_manifest_reference
        from ..storage.delivery import verify_version
        from ..runtime import sha256
        self._known_run(run_id)
        with closing(self.workspace.runtime.connect()) as db:
            row = db.execute("SELECT d.delivery_id,d.status,a.manifest_json,r.output FROM deliveries d JOIN artifact_sets a ON a.artifact_set_id=d.artifact_set_id JOIN runs r ON r.run_id=d.run_id WHERE d.run_id=? AND d.status IN ('published','superseded') ORDER BY d.published_at DESC LIMIT 1", (run_id,)).fetchone()
        if row is None:
            return []
        manifest_path = resolve_manifest_reference(self.workspace.runtime, row['manifest_json'])
        if not manifest_path.is_relative_to(self.workspace.root):
            from ..storage.delivery import delivery_root
            output = Path(row['output']).resolve()
            if output not in self.authorized_external_paths or not manifest_path.is_relative_to(delivery_root(output)):
                raise FlowError('PERMISSION_DENIED', '交付版本位於未授權的工作區外路徑')
        manifest = read_json(manifest_path)
        if manifest_path.name not in {'delivery-manifest.json', 'deliverables.json', 'report.json'}:
            raise FlowError('DELIVERY_INVALID', '未識別的交付清單格式')
        if manifest_path.name == 'delivery-manifest.json' and not verify_version(manifest_path.parent):
            raise FlowError('DELIVERY_INVALID', '版本化交付已遺失或變更')
        if manifest_path.name == 'deliverables.json':
            root = manifest_path.parent.resolve()
            for item in manifest.get('files', []):
                target = (root / item['path']).resolve()
                if (not target.is_relative_to(root) or not target.is_file() or
                        target.stat().st_size != item['size_bytes'] or sha256(target) != item['sha256']):
                    raise FlowError('DELIVERY_INVALID', '交付索引所列檔案已變更')
        if manifest_path.name == 'report.json':
            return [{'delivery_id': row['delivery_id'], 'delivery_status': row['status'],
                     'path': 'report.json', 'size_bytes': manifest_path.stat().st_size,
                     'sha256': sha256(manifest_path)}]
        return [{'delivery_id': row['delivery_id'], 'delivery_status': row['status'],
                 'path': item['path'], 'size_bytes': item['size_bytes'], 'sha256': item['sha256']}
                for item in manifest.get('files', [])]

    def export_diagnostics(self, run_id, target, include_raw=False):
        from ..observability.commands import export_preview
        self._known_run(run_id)
        if include_raw:
            raise FlowError('PERMISSION_DENIED', 'Service 只允許匯出已遮罩診斷')
        destination = self._path(target, allow_external=True)
        return export_preview(self.workspace.runtime, run_id, destination=destination, confirm=True)

    def retry_run(self, run_id, policy='safe'):
        if policy != 'safe':
            raise FlowError('INVALID_CONFIGURATION', '只支援 safe 重試政策')
        self._known_run(run_id)
        status = self.get_run_status(run_id)
        if status['execution_status'] not in {'failed', 'interrupted'}:
            raise FlowError('STATE_CONFLICT', '只有失敗或中斷的 Run 可安全評估重試')
        if status['delivery_status'] == 'published':
            raise FlowError('RETRY_UNSAFE', '此 Run 已正式交付，不能把未知副作用當作安全重試')
        with closing(self.workspace.runtime.connect()) as db:
            row = db.execute('SELECT r.flow_id,r.job_id,r.output,f.task_id FROM runs r JOIN flows f ON f.flow_id=r.flow_id WHERE r.run_id=?',
                             (run_id,)).fetchone()
        if row is None or not row['job_id']:
            raise FlowError('RETRY_UNSAFE', '找不到原工作規格')
        snapshot_path = self.workspace.runtime.home / 'jobs' / row['flow_id'] / row['job_id'] / 'snapshot.json'
        try:
            snapshot = read_json(snapshot_path)
        except (OSError, ValueError) as exc:
            raise FlowError('RETRY_UNSAFE', '原執行快照不足，請重新計畫') from exc
        readonly_task = self.workspace.task(row['task_id'], read_only=True)
        if snapshot.get('task_hash') != readonly_task.fingerprint:
            raise FlowError('RETRY_UNSAFE', '流程套件版本已改變，請重新計畫')
        workflow = snapshot.get('workflow')
        if not isinstance(workflow, dict) or any(step.get('side_effects') in {'local_write','external'} or
                                                  step.get('retry_policy') == 'none'
                                                  for step in workflow.get('steps', [])):
            raise FlowError('RETRY_UNSAFE', '流程含副作用或禁止重試的步驟')
        location = snapshot.get('source') or {}
        relative = location.get('workspace_relative')
        if not relative or '..' in Path(relative).parts:
            raise FlowError('RETRY_UNSAFE', '外部輸入需要重新確認授權與規格')
        source = (self.workspace.root / relative).resolve()
        output = Path(row['output']).resolve()
        if not source.is_relative_to(self.workspace.root) or not output.is_relative_to(self.workspace.root):
            raise FlowError('RETRY_UNSAFE', '工作區以外路徑需要人工重新確認')
        retry_key = 'retry:' + run_id
        with closing(self.workspace.runtime.connect()) as db:
            prior = db.execute('SELECT run_id,execution_spec_hash FROM run_executions WHERE idempotency_key=?',
                               (retry_key,)).fetchone()
        if prior is not None:
            spec_file = self.workspace.runtime.home / 'run_specs' / f"{prior['run_id']}.json"
            try:
                submitted = read_json(spec_file)
            except (OSError, ValueError) as exc:
                raise FlowError('RETRY_UNSAFE', '重試 Run 的提交規格不可驗證') from exc
            if (prior['execution_spec_hash'] != status['execution_spec_hash'] or
                    submitted.get('task') != row['task_id'] or
                    submitted.get('source') != str(source) or
                    submitted.get('output') != str(output) or
                    submitted.get('overrides') != workflow):
                raise FlowError('IDEMPOTENCY_CONFLICT', '既有重試 Run 的規格與原 Run 不符')
            return StartResult(run_id=prior['run_id'],
                               status=self.get_run_status(prior['run_id'])['execution_status'],
                               submitted_at=datetime.now(timezone.utc).isoformat(),
                               execution_spec_hash=prior['execution_spec_hash'])
        for offline in (False, True):
            planned = self.plan_workflow(PlanRequest(task=row['task_id'], source=source, output=output,
                                                      overrides=workflow, offline=offline))
            if planned.execution_spec_hash == status['execution_spec_hash'] and not planned.plan.get('blocked'):
                return self.start_workflow(StartRequest(task=row['task_id'], source=source, output=output,
                    overrides=workflow, offline=offline, idempotency_key=retry_key,
                    expected_spec_hash=status['execution_spec_hash']))
        raise FlowError('RETRY_UNSAFE', '輸入、設定或依賴已改變，請重新計畫後提交新 Run')
