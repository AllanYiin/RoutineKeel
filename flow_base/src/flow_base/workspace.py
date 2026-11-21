"""Workspace paths and declaration-only workflow discovery."""
from __future__ import annotations

import importlib
import os
import re
import shutil
import sys
import tempfile
import zipfile
import uuid
import keyword
import sqlite3
from contextlib import closing, nullcontext
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, model_validator, field_validator

from .contracts import Contract
from .runtime import FlowError, Runtime, atomic_json, read_json, sha256


class Manifest(Contract):
    schema_version: Literal[1] = 1
    runtime_api: Literal['0.2'] = '0.2'
    output_layout: Literal['flat-v1', 'categorized-v1'] = 'flat-v1'
    outputs: dict[str, str] = Field(default_factory=dict)
    id: str | None = Field(default=None, pattern=r'^[A-Za-z][A-Za-z0-9_.-]{0,127}$')
    module: str | None = Field(default=None, pattern=r'^[A-Za-z_][A-Za-z0-9_]*$')
    llm_policy: Literal['optional', 'forbidden'] = 'optional'
    actions: list[str] = Field(default_factory=list, max_length=40)
    recovery_action: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9_-]{0,63}$')
    version: str = Field(pattern=r'^\d+\.\d+\.\d+$')
    title: str
    description: str
    package: str = Field(pattern=r'^[A-Za-z0-9_.-]+\.whl$')
    sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    keywords: list[str] = Field(min_length=1, max_length=40)
    examples: list[str] = Field(default_factory=list, max_length=10)
    input_extensions: list[str] = Field(default_factory=list)
    parameter_paths: list[str] = Field(default_factory=list)
    rules: list[dict] = Field(default_factory=list)
    defaults: dict = Field(default_factory=dict)

    @field_validator('outputs')
    @classmethod
    def safe_named_outputs(cls, value):
        for name, step_id in value.items():
            if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', name) or name in {'.', '..'} or
                    not re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', step_id)):
                raise ValueError('Manifest outputs require safe names and Step IDs')
        return value

    @model_validator(mode='after')
    def validate_module(self):
        if self.recovery_action and self.recovery_action not in self.actions:
            raise ValueError('recovery_action 必須是 actions 宣告的能力')
        module = self.module or self.id
        if not module or not module.isidentifier() or keyword.iskeyword(module) or module == 'flow_base':
            raise ValueError('請提供合法的 module（流程套件的 Python namespace）；舊流程可沿用 id。')
        return self

    @property
    def python_module(self):
        return self.module or self.id


class Workspace:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get('FLOW_BASE_WORKSPACE') or Path.cwd()).expanduser().resolve()
        self.nested_layout = (self.root / 'workflows').is_dir()
        self.workflows_root = self.root / 'workflows' if self.nested_layout else self.root
        base = self.root / 'flow_base' if self.nested_layout else self.root
        for directory in (self.workflows_root, base):
            if directory.is_symlink() or getattr(directory, 'is_junction', lambda: False)():
                raise FlowError('TASK_FOLDER', '工作區結構不得使用連結', path=str(directory))
        self.runtime = Runtime(base / 'share_states')
        self.runtime.logs = base / 'share_logs'

    def initialize(self):
        self.runtime.initialize()
        self.runtime.logs.mkdir(parents=True, exist_ok=True)

    def discover(self, *, read_only=False):
        from .package_updates import ensure_no_pending
        ensure_no_pending(self)
        result = {}
        if not self.root.is_dir():
            return result
        for folder in sorted(self.workflows_root.iterdir()):
            if not folder.is_dir() or folder.name.startswith('.') or folder.name in {'flow_base', 'share_states', 'share_logs'}:
                continue
            path = folder / 'flow.json'
            if not path.is_file():
                continue
            if folder.is_symlink() or path.is_symlink():
                raise FlowError('TASK_FOLDER', '流程資料夾及 flow.json 不得是符號連結', path=str(folder))
            manifest = Manifest.model_validate(read_json(path))
            if manifest.id is None:
                from .runtime import digest
                generated = 'workflow_' + digest({'folder': folder.name, 'module': manifest.python_module,
                                                   'package': manifest.sha256})[:32]
                if read_only:
                    data = manifest.model_dump()
                    data['id'] = generated
                    manifest = Manifest.model_validate(data)
                else:
                    with self.runtime.lock('manifest:' + str(path), timeout=30):
                        data = read_json(path)
                        manifest = Manifest.model_validate(data)
                        if manifest.id is None:
                            data['id'] = generated
                            atomic_json(path, data)
                            manifest = Manifest.model_validate(data)
            if manifest.id in result:
                raise FlowError('TASK_DUPLICATE', '兩個資料夾包含同一流程識別碼；請保留一份，避免混用歷史',
                                folders=[str(result[manifest.id].folder), str(folder)])
            task = Task(folder, manifest)
            result[manifest.id] = task
        if read_only:
            from .runtime import digest
            known = {}
            database = self.runtime.home / 'state.sqlite3'
            if database.is_file():
                try:
                    with closing(sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
                        known = dict(db.execute('SELECT task_id,flow_id FROM flows'))
                except sqlite3.Error:
                    pass
            for task in result.values():
                task.flow_id = known.get(task.manifest.id) or 'flow_' + digest({'task_id': task.manifest.id})[:32]
                task.flow_id_status = 'registered' if task.manifest.id in known else 'provisional'
        else:
            for task in result.values():
                m = task.manifest
                task.flow_id = self.runtime.register_flow(m.id, m.version, task.fingerprint, m.title)
        return result

    def task(self, name, *, read_only=False):
        tasks = self.discover(read_only=read_only)
        for task in tasks.values():
            if task.flow_id == name:
                return task
        matches = [t for t in tasks.values() if name in {t.manifest.id, t.manifest.title, t.folder.name}]
        if len(matches) > 1:
            raise FlowError('TASK_AMBIGUOUS', '流程名稱重複，請使用唯一資料夾名稱或 flow_id',
                            choices=[{'title': t.manifest.title, 'folder': t.folder.name, 'flow_id': t.flow_id} for t in matches])
        if not matches:
            raise FlowError('TASK_UNKNOWN', 'Workflow is not installed in this workspace', task=name,
                            installed=[t.manifest.title for t in tasks.values()])
        return matches[0]

    def task_at(self, folder):
        supplied = Path(folder).expanduser()
        if supplied.is_symlink():
            raise FlowError('TASK_FOLDER', '流程入口不可使用符號連結')
        target = supplied.resolve()
        if target.parent != self.workflows_root:
            raise FlowError('TASK_FOLDER', '流程包應放在此工作區的流程目錄內', path=str(target))
        for task in self.discover().values():
            if task.folder == target:
                return task
        raise FlowError('TASK_UNKNOWN', '流程資料夾缺少有效的 flow.json', path=str(target))


class Task:
    def __init__(self, folder, manifest):
        self.folder, self.manifest = folder, manifest
        self._adapter = None

    @property
    def fingerprint(self):
        from .runtime import digest
        return digest(self.manifest.model_dump())

    def load(self, runtime, *, cache_root=None):
        if self._adapter:
            return self._adapter
        m = self.manifest
        wheel = self.folder / m.package
        if not wheel.is_file() or wheel.is_symlink() or sha256(wheel) != m.sha256:
            raise FlowError('TASK_INTEGRITY', 'Task package is missing or differs from flow.json', task=m.id)
        if cache_root is not None:
            Path(cache_root).mkdir(parents=True, exist_ok=True)
        root = (Path(cache_root) if cache_root is not None else runtime.home / 'plugins') / m.sha256
        lock = nullcontext() if cache_root is not None else runtime.lock('plugin:' + m.sha256, timeout=60)
        with lock:
            # Validate the extracted copy as well; a wheel hash alone does not protect cache edits.
            with zipfile.ZipFile(wheel) as z:
                members = z.infolist()
                if len(members) > 3000 or sum(i.file_size for i in members) > 32 * 1024**2:
                    raise FlowError('TASK_ARCHIVE', 'Task package is oversized; large resources belong in the resource catalogue')
                names = [i.filename for i in members]
                if len(set(names)) != len(names):
                    raise FlowError('TASK_ARCHIVE', 'Duplicate entries')
                import hashlib
                files = {}
                for info in members:
                    p = PurePosixPath(info.filename)
                    if p.is_absolute() or '..' in p.parts or '\\' in info.filename or ':' in info.filename or not p.parts:
                        raise FlowError('TASK_ARCHIVE', 'Unsafe task package path')
                    if p.parts[0] != m.python_module and not (p.parts[0].startswith(m.python_module + '-') and p.parts[0].endswith('.dist-info')):
                        raise FlowError('TASK_ARCHIVE', 'Task wheel must contain only its own Python namespace')
                    if (info.external_attr >> 16) & 0o170000 == 0o120000:
                        raise FlowError('TASK_ARCHIVE', 'Symlinks are not supported')
                    if not info.is_dir():
                        files[info.filename] = z.read(info)
                valid = root.is_dir() and all((root / n).is_file() and not (root / n).is_symlink()
                         and sha256(root / n) == hashlib.sha256(b).hexdigest() for n, b in files.items())
                if valid:
                    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
                    valid = actual == set(files)
                if not valid:
                    if root.exists():
                        shutil.rmtree(root)
                    staging = Path(tempfile.mkdtemp(prefix='task-', dir=root.parent if root.parent.exists() else runtime.home))
                    try:
                        for name, body in files.items():
                            target = staging / name
                            target.parent.mkdir(parents=True, exist_ok=True)
                            target.write_bytes(body)
                        root.parent.mkdir(parents=True, exist_ok=True)
                        staging.rename(root)
                    finally:
                        if staging.exists():
                            shutil.rmtree(staging)
        if m.id and m.python_module == 'rk_bp_' + m.id:
            from .blueprints.adapter import ADAPTER_SOURCE, BlueprintAdapter
            if files.get(m.python_module + '/adapter.py') == ADAPTER_SOURCE and files.get(m.python_module + '/__init__.py') == b'':
                # Data-only blueprint revisions have no wheel module to retain in sys.modules.
                self._adapter = BlueprintAdapter(root / m.python_module)
                return self._adapter
        loaded = sys.modules.get(m.python_module)
        if loaded and not Path(loaded.__file__).resolve().is_relative_to(root.resolve()):
            raise FlowError('TASK_RESTART', 'Task changed during this session; restart flow_base to load the new version')
        sys.path.insert(0, str(root))
        module = importlib.import_module(m.python_module + '.adapter')
        self._adapter = module.Adapter()
        return self._adapter

    def configuration(self, runtime, overrides=None):
        from .engine import merge
        settings = self.folder / 'settings.json'
        if settings.is_symlink():
            raise FlowError('TASK_CONFIG', 'settings.json 不得是符號連結')
        local = read_json(settings) if settings.is_file() else {}
        if not isinstance(local, dict):
            raise FlowError('TASK_CONFIG', 'settings.json 必須是 JSON object')
        adapter = self.load(runtime)
        workflow = adapter.configuration(merge(merge(self.manifest.defaults, local), overrides or {}))
        if hasattr(adapter, 'resolve_environment'):
            from .runtime import inspect_environment
            workflow = adapter.resolve_environment(workflow, inspect_environment(runtime.home))
        return workflow

    def validate_intent_overrides(self, overrides):
        allowed = set(self.manifest.parameter_paths)
        def visit(value, prefix=''):
            for key, val in value.items():
                path = prefix + key
                if isinstance(val, dict):
                    visit(val, path + '.')
                elif path not in allowed:
                    raise FlowError('INTENT_PARAMETER', 'Natural language intent contains an undeclared parameter', parameter=path)
        visit(overrides)

    def invoke_action(self, runtime, name, parameters):
        if name not in self.manifest.actions:
            raise FlowError('TASK_ACTION_UNKNOWN', '工作流程未宣告此能力', action=name)
        adapter = self.load(runtime)
        if not hasattr(adapter, 'invoke_action'):
            raise FlowError('TASK_CAPABILITY', '工作流程未實作能力派送')
        run_id = runtime.start_run(self.flow_id, 'action:' + name, self.folder)
        log = runtime.logs / self.flow_id / run_id
        atomic_json(log / 'run.json', {'flow_id': self.flow_id, 'run_id': run_id, 'action': name, 'status': 'running'})
        from .presentation import ACTIVE, Reporter
        from .core.run_context import RunContext, ACTIVE_RUN
        from types import SimpleNamespace
        reporter = Reporter(self.manifest.title + '｜' + name, self.flow_id, run_id, log, 1, runtime)
        from .core.lifecycle import RunStore
        from .execution.supervisor import Supervisor
        action_payload = {'flow_id': self.flow_id, 'run_id': run_id, 'action': name,
                          'folder': self.folder, 'logdir': log}
        run_token = ACTIVE_RUN.set(RunContext(runtime, run_id, log, reporter,
                                              event_sink=reporter.event_recorder, action=name, action_payload=action_payload))
        reporter_token = ACTIVE.set(reporter)
        store = RunStore(runtime)
        supervisor = None
        attempt_id = None
        attempt_finished = False
        try:
            store.transition(run_id, 'created', 'queued')
            supervisor = Supervisor(runtime, run_id, ACTIVE_RUN.get())
            supervisor.__enter__()
            store.transition(run_id, 'preparing', 'running')
            reporter.event_recorder.record('run.started')
            reporter.start()
            attempt_id = store.create_attempt(run_id, name)
            ACTIVE_RUN.get().attempt_id = attempt_id
            store.transition_attempt(attempt_id, 'pending', 'running')
            reporter.event_recorder.record('step.started', attributes={'step_id': name})
            reporter.enter(SimpleNamespace(id=name, handler='adapter.invoke_action'), 1, name)
            result = adapter.invoke_action(runtime, name, parameters)
            store.transition_attempt(attempt_id, 'running', 'succeeded')
            attempt_finished = True
            reporter.event_recorder.record('step.finished', attributes={'step_id': name})
            if supervisor.heartbeat_error:
                raise FlowError('SUPERVISOR_HEARTBEAT', '執行期間無法更新監督程序 lease') from supervisor.heartbeat_error
            if ACTIVE_RUN.get().cancel_requested.is_set():
                raise FlowError('RUN_CANCEL_REQUESTED', '已確認取消；停止成果發布')
            status = result.get('status', result.get('decision', 'complete')) if isinstance(result, dict) else 'complete'
            report = {'flow_id': self.flow_id, 'run_id': run_id, 'action': name, 'result': result,
                      'status': status, 'token_usage': reporter.usage.finish(), 'presentation': reporter.references()}
            reporter.event_recorder.record('delivery.staged')
            atomic_json(log / 'report.json', report)
            store.transition(run_id, 'running', 'publishing')
            store.record_delivery(run_id, log / 'report.json')
            reporter.event_recorder.record('delivery.verified', attributes={'status': 'verified'})
            store.transition(run_id, 'publishing', 'succeeded', result_status=str(status).lower(), delivery_status='published')
            reporter.event_recorder.record('delivery.published', attributes={'result_status': str(status).lower()})
            reporter.event_recorder.record('run.finished', attributes={'result_status': str(status).lower()})
            try:
                reporter.complete('executed')
                reporter.end(report)
                runtime.update_run(run_id, status, finished=True)
                atomic_json(log / 'run.json', {'flow_id': self.flow_id, 'run_id': run_id, 'action': name, 'status': status})
            except Exception as post_error:
                from .diagnostics import record_exception
                try:
                    report['post_delivery_diagnostic'] = record_exception(post_error, log / 'post_delivery', run_id=run_id)['diagnostic_json']
                except Exception:
                    report['post_delivery_diagnostic'] = 'unavailable'
            return report
        except BaseException as exc:
            from .diagnostics import record_exception, launcher_id
            from .observability.diagnostics import guard_secondary
            code = exc.code if isinstance(exc, FlowError) else type(exc).__name__
            cancelled = isinstance(exc, FlowError) and exc.code in {'PROCESS_CANCELLED', 'RUN_CANCEL_REQUESTED'}
            status = 'cancelled' if cancelled else ('interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed')
            if attempt_id and not attempt_finished:
                try:
                    reporter.event_recorder.record('step.failed', severity='ERROR',
                                                   attributes={'step_id': name, 'code': code})
                except Exception:
                    pass
            try:
                reporter.event_recorder.record('run.interrupted' if isinstance(exc, KeyboardInterrupt) else ('run.cancelled' if cancelled else 'run.failed'),
                                               severity='ERROR', attributes={'code': code})
            except Exception:
                pass
            diagnostic = record_exception(exc, log, flow_id=self.flow_id, run_id=run_id,
                                          action=name, launch_id=launcher_id(runtime))
            guard_secondary(exc, diagnostic, 'finish_attempt', lambda: store.finish_attempt_failure(
                attempt_id, interrupted=isinstance(exc, KeyboardInterrupt), cancelled=cancelled,
                error_id=diagnostic['error_id']), runtime=runtime)
            guard_secondary(exc, diagnostic, 'finish_failure', lambda: store.finish_failure(run_id,
                interrupted=isinstance(exc, KeyboardInterrupt), cancelled=cancelled, error_id=diagnostic['error_id']), runtime=runtime)
            state = guard_secondary(exc, diagnostic, 'read_run_status', lambda: store.get(run_id), runtime=runtime)
            status = state['execution_status'] if state else status
            guard_secondary(exc, diagnostic, 'reporter.fail', lambda: reporter.fail(code, diagnostic['message'],
                interrupted=isinstance(exc, KeyboardInterrupt), diagnostic=diagnostic), runtime=runtime)
            guard_secondary(exc, diagnostic, 'update_run', lambda: runtime.update_run(run_id, status, error=code, finished=True), runtime=runtime)
            guard_secondary(exc, diagnostic, 'failure.json', lambda: atomic_json(log / 'failure.json',
                {'flow_id': self.flow_id, 'run_id': run_id, 'action': name, 'error': code,
                 'error_details': diagnostic, 'token_usage': reporter.usage.finish(),
                 'presentation': reporter.references()}), runtime=runtime)
            guard_secondary(exc, diagnostic, 'run.json', lambda: atomic_json(log / 'run.json',
                {'flow_id': self.flow_id, 'run_id': run_id, 'action': name, 'status': status}), runtime=runtime)
            raise
        finally:
            if supervisor is not None:
                try:
                    supervisor.__exit__(None, None, None)
                except BaseException as cleanup_error:
                    from .diagnostics import record_exception
                    record_exception(cleanup_error, log / 'cleanup', runtime=runtime, run_id=run_id)
            ACTIVE.reset(reporter_token)
            ACTIVE_RUN.reset(run_token)


def catalog(workspace):
    return [{**t.manifest.model_dump(exclude={'sha256', 'package', 'defaults', 'rules'}), 'flow_id': t.flow_id, 'folder': t.folder.name}
            for t in workspace.discover().values()]
