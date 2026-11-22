"""Task-neutral execution, checkpointing, output ownership and content caches."""
from __future__ import annotations

import dataclasses
import uuid
from pathlib import Path
from typing import ClassVar

from flow_base.observability.recovery import SecondaryOperation

from . import __version__
from .core.lifecycle import RunStore
from .core.run_context import ACTIVE_RUN, RunContext
from .diagnostics import launcher_id, record_exception
from .execution.supervisor import Supervisor
from .observability.diagnostics import guard_secondary
from .presentation import ACTIVE, Reporter
from .runtime import (
    FlowError,
    Runtime,
    atomic_json,
    digest,
    read_json,
    require_space,
    sha256,
)


def merge(base, patch):
    result = dict(base)
    for key, value in patch.items():
        result[key] = merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


@dataclasses.dataclass
class Context:
    runtime: Runtime
    workflow: object
    source: Path
    workdir: Path
    job_id: str
    prepared: dict
    offline: bool = False
    logdir: Path | None = None
    flow_id: str | None = None
    run_id: str | None = None


class Adapter:
    """Public task API 0.2. Override domain behavior; runtime remains unaware of it."""
    handlers: ClassVar[dict] = {}

    def configuration(self, overrides):
        raise NotImplementedError

    def plan(self, runtime, workflow, source=None, output=None):
        blocked = []
        if source is not None and not source.is_file():
            blocked.append({'code': 'INPUT_MISSING', 'path': str(source)})
        return {'workflow': workflow.id, 'blocked': blocked, 'actions': [], 'ready_to_prepare': not blocked}

    def prepare(self, runtime, workflow, offline=False):
        return {'ok': True, 'profile': 'core'}

    def verify(self, runtime, workflow):
        return {'ok': True, 'profile': 'core'}

    def pre_run(self, runtime, workflow, source, offline):
        pass

    def inspect_input(self, runtime, workflow, source):
        pass

    def step_settings(self, workflow, handler, prepared):
        return workflow.options.model_dump()

    def profile_key(self, runtime, workflow):
        return None

    def minimum_space(self, workflow):
        return 0

    def job_scoped(self, handler):
        return False

    def publish(self, terminal, output, workflow):
        from .runtime import atomic_copy
        atomic_copy(terminal, output / 'result.bin')
        return {'status': 'complete'}

    def publish_outputs(self, terminal, output, workflow):
        """Categorized contract. Override to deliver docs/scripts as well as artifacts."""
        from .outputs import artifact_directory
        return self.publish(terminal, artifact_directory(output, 'categorized-v1'), workflow)


def plan(runtime, task, workflow, source=None, output=None):
    from .outputs import choose_output, output_layout
    default_output = choose_output(runtime, task, source)
    output = choose_output(runtime, task, source, output)
    adapter = task.load(runtime)
    result = adapter.plan(runtime, workflow, source, output)
    for step in workflow.steps:
        if step.handler not in adapter.handlers:
            result['blocked'].append({'code': 'HANDLER_MISSING', 'handler': step.handler})
    declared_outputs = getattr(workflow, 'outputs', {})
    if declared_outputs != task.manifest.outputs:
        result['blocked'].append({'code': 'OUTPUT_DECLARATION',
                                  'message': 'Workflow outputs 必須與已安裝 Manifest 的宣告一致'})
    if not declared_outputs:
        result['warnings'] = [*result.get('warnings', []),
                              '沿用最後 Step 作為 legacy 單一輸出；新流程請宣告具名 outputs。']
    result['ready_to_prepare'] = not result['blocked']
    result.update(task=task.manifest.id, flow_id=task.flow_id, task_version=task.manifest.version)
    result.update(output=str(output) if output else None,
                  output_selection='workflow_default' if output == default_output else 'explicit',
                  output_layout=output_layout(output, task.manifest.output_layout) if output else task.manifest.output_layout)
    return result


def _execution_identity(runtime, task, source, output):
    """Match the historical job key while allowing the Run to be recorded first."""
    from .outputs import output_layout
    source, output = Path(source).resolve(), Path(output).resolve()
    input_hash = sha256(source) if source.is_file() else None
    def location(path):
        try:
            return {'workspace_relative': path.relative_to(runtime.workspace_root).as_posix()}
        except ValueError:
            return {'external_absolute': str(path)}
    output_identity = location(output)
    if output.is_relative_to((task.folder / 'outputs').resolve()) and output_layout(output, task.manifest.output_layout) == 'categorized-v1':
        output_identity = {'flow_relative': output.relative_to(task.folder.resolve()).as_posix()}
    job = digest({'flow_id': task.flow_id, 'input': input_hash, 'output': output_identity})[:32]
    return job, input_hash, location


def _execution_spec_hash(runtime, task, workflow, input_hash, output, offline):
    adapter = task.load(runtime)
    profile_key = adapter.profile_key(runtime, workflow)
    effective = workflow.model_dump()
    selection = effective.get('execution_selection')
    if isinstance(selection, dict):
        # Free RAM/disk/VRAM and probe timestamps are evidence, not execution
        # semantics. Keep the requested and selected policy for identity.
        effective['execution_selection'] = {key: selection[key] for key in
            ('policy_version', 'requested', 'selected') if key in selection}
    return digest({'revision': task.fingerprint, 'workflow': effective,
                   'input_hash': input_hash, 'output': str(Path(output).resolve()),
                   'profile_key': profile_key, 'offline': offline})


def run_demo(runtime, task, output):
    from .outputs import (
        artifact_directory,
        choose_output,
        validate_tree,
        write_delivery_index,
    )
    output = choose_output(runtime, task, requested=output)
    validate_tree(output)
    if output.exists() and any(output.iterdir()):
        raise FlowError('OUTPUT_EXISTS', 'Demo output must be empty')
    run_id = runtime.start_run(task.flow_id, 'demonstration_fixture', output)
    logs = runtime.logs / task.flow_id / run_id
    from types import SimpleNamespace
    reporter = Reporter(task.manifest.title + '｜示範資料（非正式處理）', task.flow_id, run_id, logs, 1, runtime)
    run_token = ACTIVE_RUN.set(RunContext(runtime, run_id, logs, reporter, event_sink=reporter.event_recorder))
    reporter_token = ACTIVE.set(reporter)
    store = RunStore(runtime)
    supervisor = None
    attempt_id = None
    attempt_finished = False
    try:
        store.transition(run_id, 'created', 'queued')
        supervisor = Supervisor(runtime, run_id, ACTIVE_RUN.get())
        supervisor.__enter__()
        reporter.event_recorder.record('run.preparing')
        store.transition(run_id, 'preparing', 'running')
        reporter.event_recorder.record('run.started')
        reporter.start()
        attempt_id = store.create_attempt(run_id, 'demo')
        ACTIVE_RUN.get().attempt_id = attempt_id
        store.transition_attempt(attempt_id, 'pending', 'running')
        reporter.event_recorder.record('step.started', attributes={'step_id': 'demo'})
        reporter.enter(SimpleNamespace(id='demo', handler='adapter.demo'), 1, '建立示範資料')
        result = task.load(runtime).demo(runtime, artifact_directory(output, task.manifest.output_layout))
        store.transition_attempt(attempt_id, 'running', 'succeeded')
        attempt_finished = True
        reporter.event_recorder.record('step.finished', attributes={'step_id': 'demo'})
        if supervisor.heartbeat_error:
            raise FlowError('SUPERVISOR_HEARTBEAT', '執行期間無法更新監督程序 lease') from supervisor.heartbeat_error
        if ACTIVE_RUN.get().cancel_requested.is_set():
            raise FlowError('RUN_CANCEL_REQUESTED', '已確認取消；停止成果發布')
        result.update(flow_id=task.flow_id, run_id=run_id, output=str(output), output_layout=task.manifest.output_layout)
        result['token_usage'] = reporter.usage.finish()
        result['presentation'] = reporter.references()
        reporter.event_recorder.record('delivery.staged', attributes={'output_layout': task.manifest.output_layout})
        atomic_json(output / 'report.json', result)
        write_delivery_index(output, result)
        atomic_json(logs / 'report.json', result)
        store.transition(run_id, 'running', 'publishing')
        delivery_index = output / 'deliverables.json'
        store.record_delivery(run_id, delivery_index if delivery_index.is_file() else output / 'report.json')
        reporter.event_recorder.record('delivery.verified', attributes={'status': 'verified'})
        store.transition(run_id, 'publishing', 'succeeded', result_status='demonstration', delivery_status='published')
        reporter.event_recorder.record('delivery.published', attributes={'result_status': 'demonstration'})
        reporter.event_recorder.record('run.finished', attributes={'result_status': 'demonstration'})
        with SecondaryOperation('runtime.update_run') as update_run_outcome:
            runtime.update_run(run_id, 'complete', finished=True)
            reporter.complete('executed')
            reporter.end(result, {'label': '示範結束：未執行正式處理，不能當作驗收證據。'})
        if update_run_outcome.error is not None:
            post_error = update_run_outcome.error
            with SecondaryOperation('record_exception') as diagnostic_write:
                result['post_delivery_diagnostic'] = record_exception(post_error, logs / 'post_delivery', run_id=run_id)['diagnostic_json']
            if diagnostic_write.error is not None:
                result['post_delivery_diagnostic'] = 'unavailable'
        return result
    except BaseException as exc:
        cancelled = isinstance(exc, FlowError) and exc.code in {'PROCESS_CANCELLED', 'RUN_CANCEL_REQUESTED'}
        status = 'cancelled' if cancelled else ('interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed')
        code = exc.code if isinstance(exc, FlowError) else type(exc).__name__
        if attempt_id and not attempt_finished:
            with SecondaryOperation('reporter.event_recorder.record'):
                reporter.event_recorder.record('step.failed', severity='ERROR',
                                               attributes={'step_id': 'demo', 'code': code})
        with SecondaryOperation('reporter.event_recorder.record'):
            reporter.event_recorder.record('run.interrupted' if isinstance(exc, KeyboardInterrupt) else ('run.cancelled' if cancelled else 'run.failed'),
                                           severity='ERROR', attributes={'code': code})
        diagnostic = record_exception(exc, logs, flow_id=task.flow_id, run_id=run_id, launch_id=launcher_id(runtime))
        guard_secondary(exc, diagnostic, 'finish_attempt', lambda exc=exc: store.finish_attempt_failure(
            attempt_id, interrupted=isinstance(exc, KeyboardInterrupt), cancelled=cancelled,
            error_id=diagnostic['error_id']), runtime=runtime)
        guard_secondary(exc, diagnostic, 'finish_failure', lambda exc=exc: store.finish_failure(run_id,
            interrupted=isinstance(exc, KeyboardInterrupt), cancelled=cancelled, error_id=diagnostic['error_id']), runtime=runtime)
        state = guard_secondary(exc, diagnostic, 'read_run_status', lambda: store.get(run_id), runtime=runtime)
        status = state['execution_status'] if state else status
        guard_secondary(exc, diagnostic, 'reporter.fail', lambda exc=exc: reporter.fail(code, diagnostic['message'],
            interrupted=isinstance(exc, KeyboardInterrupt), diagnostic=diagnostic), runtime=runtime)
        guard_secondary(exc, diagnostic, 'update_run', lambda: runtime.update_run(run_id, status, error=code, finished=True), runtime=runtime)
        guard_secondary(exc, diagnostic, 'failure.json', lambda: atomic_json(logs / 'failure.json',
            {'flow_id': task.flow_id, 'run_id': run_id, 'status': status, 'error': code, 'error_details': diagnostic,
             'token_usage': reporter.usage.finish(), 'presentation': reporter.references()}), runtime=runtime)
        if isinstance(exc, FlowError):
            guard_secondary(exc, diagnostic, 'attach_error_ids',
                lambda exc=exc: exc.details.update(flow_id=task.flow_id, run_id=run_id), runtime=runtime)
        raise
    finally:
        if supervisor is not None:
            with SecondaryOperation('supervisor.__exit__', catch=BaseException) as supervisor_cleanup:
                supervisor.__exit__(None, None, None)
            if supervisor_cleanup.error is not None:
                cleanup_error = supervisor_cleanup.error
                record_exception(cleanup_error, logs / 'cleanup', runtime=runtime, run_id=run_id)
        ACTIVE.reset(reporter_token)
        ACTIVE_RUN.reset(run_token)


def reserve_workflow_run(runtime, task, workflow, source, output=None, *, offline=False, idempotency_key=None):
    from .outputs import choose_output
    output = choose_output(runtime, task, source, output)
    job_id, input_hash, _ = _execution_identity(runtime, task, source, output)
    spec_hash = _execution_spec_hash(runtime, task, workflow, input_hash, output, offline)
    run_id = runtime.start_run(task.flow_id, source, output, job_id=job_id, spec_hash=spec_hash,
                               idempotency_key=idempotency_key)
    return run_id, output, spec_hash


def run_workflow(runtime, task, workflow, source, output=None, *, offline=False, prepared=None, reserved_run_id=None):
    from .outputs import choose_output
    output = choose_output(runtime, task, source, output)
    job_id, input_hash, _ = _execution_identity(runtime, task, source, output)
    spec_hash = _execution_spec_hash(runtime, task, workflow, input_hash, output, offline)
    if reserved_run_id is None:
        run_id = runtime.start_run(task.flow_id, source, output, job_id=job_id, spec_hash=spec_hash)
    else:
        execution = RunStore(runtime).get(reserved_run_id)
        if execution is None or execution['execution_status'] != 'created' or execution['job_id'] != job_id or execution['execution_spec_hash'] != spec_hash:
            raise FlowError('RUN_SPEC_MISMATCH', '保留的 Run 與目前執行規格不一致', run_id=reserved_run_id)
        run_id = reserved_run_id
    logs = runtime.logs / task.flow_id / run_id
    logs.mkdir(parents=True, exist_ok=True)
    atomic_json(logs / 'run.json', {'flow_id': task.flow_id, 'run_id': run_id, 'task': task.manifest.id, 'status': 'starting'})
    reporter = Reporter(task.manifest.title, task.flow_id, run_id, logs, len(workflow.steps), runtime)
    run_token = ACTIVE_RUN.set(RunContext(runtime, run_id, logs, reporter, event_sink=reporter.event_recorder))
    reporter_token = ACTIVE.set(reporter)
    store = RunStore(runtime)
    supervisor = None
    try:
        store.transition(run_id, 'created', 'queued')
        supervisor = Supervisor(runtime, run_id, ACTIVE_RUN.get())
        supervisor.__enter__()
        reporter.event_recorder.record('run.preparing')
        reporter.start()
        report = _run_workflow(runtime, task, workflow, source, output, run_id=run_id, offline=offline, prepared=prepared)
        with SecondaryOperation('finalize published run') as post_delivery_update:
            if supervisor.heartbeat_error:
                raise FlowError('SUPERVISOR_HEARTBEAT', '執行期間無法更新監督程序 lease') from supervisor.heartbeat_error
            if store.get(run_id)['execution_status'] != 'succeeded':
                raise FlowError('DELIVERY_INCOMPLETE', '交付與 Run 終態尚未一致', run_id=run_id)
            reporter.event_recorder.record('delivery.preview_saved' if report.get('graph_delivery_status')=='withheld' else 'delivery.published',
                                           attributes={'result_status':str(report.get('graph_result_status',report.get('status','unknown'))).lower()})
            reporter.event_recorder.record('run.finished', attributes={'result_status': str(report.get('graph_result_status',report.get('status', 'unknown'))).lower()})
            runtime.update_run(run_id, report.get('status', 'complete'), job_id=report.get('job_id'), finished=True)
            atomic_json(logs / 'run.json', {'flow_id': task.flow_id, 'run_id': run_id, 'job_id': report.get('job_id'), 'status': report.get('status', 'complete')})
            adapter = task.load(runtime)
            summary = adapter.result_summary(report) if hasattr(adapter, 'result_summary') else None
            reporter.end(report, summary)
        if post_delivery_update.error is not None:
            post_error = post_delivery_update.error
            with SecondaryOperation('record_exception') as diagnostic_write:
                report['post_delivery_diagnostic'] = record_exception(post_error, logs / 'post_delivery', run_id=run_id)['diagnostic_json']
            if diagnostic_write.error is not None:
                report['post_delivery_diagnostic'] = 'unavailable'
        return report
    except BaseException as exc:
        cancelled = isinstance(exc, FlowError) and exc.code in {'PROCESS_CANCELLED', 'RUN_CANCEL_REQUESTED'}
        status = 'cancelled' if cancelled else ('interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed')
        code = exc.code if isinstance(exc, FlowError) else type(exc).__name__
        with SecondaryOperation('reporter.event_recorder.record'):
            reporter.event_recorder.record('run.interrupted' if isinstance(exc, KeyboardInterrupt) else ('run.cancelled' if cancelled else 'run.failed'),
                                           severity='ERROR', attributes={'code': code})
        diagnostic = record_exception(exc, logs, flow_id=task.flow_id, run_id=run_id,
                                      launch_id=launcher_id(runtime), step=reporter.step_id)
        guard_secondary(exc, diagnostic, 'finish_failure', lambda exc=exc: store.finish_failure(run_id,
            interrupted=isinstance(exc, KeyboardInterrupt), cancelled=cancelled, error_id=diagnostic['error_id']), runtime=runtime)
        state = guard_secondary(exc, diagnostic, 'read_run_status', lambda: store.get(run_id), runtime=runtime)
        status = state['execution_status'] if state else status
        guard_secondary(exc, diagnostic, 'reporter.fail', lambda exc=exc: reporter.fail(code, diagnostic['message'],
            interrupted=isinstance(exc, KeyboardInterrupt), diagnostic=diagnostic), runtime=runtime)
        guard_secondary(exc, diagnostic, 'update_run', lambda: runtime.update_run(run_id, status, error=code, finished=True), runtime=runtime)
        guard_secondary(exc, diagnostic, 'run.json', lambda: atomic_json(logs / 'run.json',
            {'flow_id': task.flow_id, 'run_id': run_id, 'task': task.manifest.id,
             'status': status, 'error': code}), runtime=runtime)
        guard_secondary(exc, diagnostic, 'failure.json', lambda: atomic_json(logs / 'failure.json',
            {'flow_id': task.flow_id, 'run_id': run_id, 'status': status, 'error': code, 'error_details': diagnostic,
             'token_usage': reporter.usage.finish(), 'presentation': reporter.references()}), runtime=runtime)
        if isinstance(exc, FlowError):
            guard_secondary(exc, diagnostic, 'attach_error_ids',
                lambda exc=exc: exc.details.update(flow_id=task.flow_id, run_id=run_id), runtime=runtime)
        raise
    finally:
        if supervisor is not None:
            with SecondaryOperation('supervisor.__exit__', catch=BaseException) as supervisor_cleanup:
                supervisor.__exit__(None, None, None)
            if supervisor_cleanup.error is not None:
                cleanup_error = supervisor_cleanup.error
                record_exception(cleanup_error, logs / 'cleanup', runtime=runtime, run_id=run_id)
        ACTIVE.reset(reporter_token)
        ACTIVE_RUN.reset(run_token)


def _run_workflow(runtime, task, workflow, source, output, *, run_id, offline=False, prepared=None):
    from .outputs import (
        artifact_directory,
        output_layout,
        validate_tree,
        write_delivery_index,
    )
    from .storage.artifacts import MANIFEST, build_artifact_set, validate_artifact_set
    from .storage.delivery import (
        delivery_root,
        prepare_staging,
        promote_staging,
        recover_transactions,
    )
    adapter = task.load(runtime)
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_file():
        raise FlowError('INPUT_MISSING', 'Input must be a regular file', path=str(source))
    if output == source or output in source.parents:
        raise FlowError('OUTPUT_CONFLICT', 'Choose a separate output directory')
    adapter.pre_run(runtime, workflow, source, offline)
    p = plan(runtime, task, workflow, source, output)
    if p['blocked']:
        raise FlowError('PREFLIGHT_BLOCKED', 'Resolve preflight issues', blockers=p['blocked'])
    runtime.initialize()
    job, input_hash, location = _execution_identity(runtime, task, source, output)
    from .core.run_context import current
    current(runtime).job_id = job
    runtime.update_run(run_id, 'waiting', job_id=job)
    work = runtime.home / 'jobs' / task.flow_id / job
    logs = runtime.logs / task.flow_id / run_id
    with runtime.lock('output:' + str(output), timeout=1):
        recover_transactions(output, RunStore(runtime))
        transactions = delivery_root(output) / 'transactions'
        if transactions.is_dir() and any(transactions.iterdir()):
            raise FlowError('DELIVERY_RECOVERY_REQUIRED', '交付交易尚未完成恢復，請先處理診斷紀錄')
        validate_tree(output)
        previous_report = output / 'report.json'
        layout = output_layout(output, task.manifest.output_layout)
        if (output.exists() and (not output.is_dir() or any(output.iterdir()))) and (not previous_report.is_file() or read_json(previous_report).get('job_id') != job):
            raise FlowError('OUTPUT_EXISTS', 'Output contains another job; choose an empty directory')
        work.mkdir(parents=True, exist_ok=True)
        logs.mkdir(parents=True, exist_ok=True)
        if hasattr(adapter, 'check_output'):
            adapter.check_output(runtime, workflow, artifact_directory(output, layout))
        stage = prepare_staging(output, run_id)
        report_path = stage / 'report.json'
        history = []
        atomic_json(report_path, {'job_id': job, 'status': 'preparing', 'runtime': __version__, 'output_layout': layout})
        try:
            runtime.update_run(run_id, 'preparing', job_id=job)
            recorder = ACTIVE.get().event_recorder
            if prepared is None:
                adapter.prepare(runtime, workflow, offline)
                adapter.inspect_input(runtime, workflow, source)
                recorder.record('resource.verify_started')
                prepared = adapter.verify(runtime, workflow)
                recorder.record('resource.verified')
            ACTIVE.get().environment_ready(prepared)
            snapshot = {'runtime': __version__, 'task': task.manifest.id, 'task_version': task.manifest.version,
                        'flow_id': task.flow_id, 'run_id': run_id,
                        'task_hash': task.fingerprint, 'job_id': job, 'workflow': workflow.model_dump(),
                        'input_hash': input_hash, 'source': location(source), 'prepared': prepared}
            atomic_json(work / 'snapshot.json', snapshot)
            ctx = Context(runtime, workflow, source, work, job, prepared, offline, logs, task.flow_id, run_id)
            runtime.update_run(run_id, 'running', job_id=job)
            store = RunStore(runtime)
            store.transition(run_id, 'preparing', 'running')
            recorder.record('run.started')
            artifacts = {}
            root_span = current(runtime).span_id
            for index, step in enumerate(workflow.steps, 1):
                if current(runtime).cancel_requested.is_set():
                    raise FlowError('RUN_CANCEL_REQUESTED', '已確認取消；停止後續節點')
                current(runtime).attempt_id = store.create_attempt(run_id, step.id)
                current(runtime).parent_span_id = root_span
                current(runtime).span_id = 'span_' + uuid.uuid4().hex
                recorder.record('step.started', attributes={'step_id': step.id})
                label = adapter.step_title(step) if hasattr(adapter, 'step_title') else step.id
                ACTIVE.get().enter(step, index, label)
                deps = {name: artifacts[name] for name in step.needs}
                key = digest({'runtime_api': '0.2', 'task_hash': task.manifest.sha256,
                              'handler': step.handler, 'parameters': step.parameters,
                              'dependencies': {k: sha256(v) for k, v in deps.items()},
                              'source': input_hash if not deps else None,
                              'settings': adapter.step_settings(workflow, step.handler, prepared),
                              'profile': adapter.profile_key(runtime, workflow),
                              'job': job if adapter.job_scoped(step.handler) else None})
                cacheable = (step.cacheable if step.cacheable is not None else
                             step.side_effects in {None, 'none'})
                declared_set = bool(step.output_artifacts) or step.id in getattr(workflow, 'outputs', {}).values()
                store.set_attempt_cache_key(current(runtime).attempt_id, key)
                with runtime.lock('step:' + key, timeout=600):
                    artifact = runtime.cached(key) if cacheable else None
                    if artifact and declared_set and validate_artifact_set(artifact, step.output_artifacts) is None:
                        artifact = None
                    if artifact and hasattr(adapter, 'artifact_valid') and not adapter.artifact_valid(runtime, step.handler, artifact):
                        artifact = None
                    if artifact:
                        status = 'cached'
                        if declared_set:
                            store.record_step_artifact_set(run_id, current(runtime).attempt_id,
                                                           key, artifact.parent / MANIFEST)
                        store.transition_attempt(current(runtime).attempt_id, 'pending', 'cached')
                        recorder.record('step.cache_hit', attributes={'step_id': step.id, 'cache_key': key})
                    else:
                        store.transition_attempt(current(runtime).attempt_id, 'pending', 'running')
                        artifact_folder = (key + '/' + run_id if cacheable and declared_set else
                                           key if cacheable else key + '-' + run_id)
                        target = runtime.home / 'artifacts' / artifact_folder / 'result.json'
                        target.parent.mkdir(parents=True, exist_ok=True)
                        atomic_json(logs / 'pending_step.json', {'flow_id': task.flow_id, 'run_id': run_id,
                            'job_id': job, 'step': step.id, 'handler': step.handler,
                            'artifact': target.relative_to(runtime.home).as_posix()})
                        require_space(target, adapter.minimum_space(workflow))
                        artifact = Path(adapter.handlers[step.handler](ctx, step, deps, target)).resolve()
                        if not artifact.is_relative_to(target.parent.resolve()) or not artifact.is_file():
                            raise FlowError('HANDLER_CONTRACT', 'Handler must return a file in its assigned artifact directory')
                        if declared_set:
                            build_artifact_set(artifact, step.output_artifacts)
                            store.record_step_artifact_set(run_id, current(runtime).attempt_id,
                                                           key, artifact.parent / MANIFEST)
                        if cacheable:
                            runtime.remember(key, artifact)
                        store.transition_attempt(current(runtime).attempt_id, 'running', 'succeeded')
                        recorder.record('step.finished', attributes={'step_id': step.id, 'artifact_hash': sha256(artifact)})
                        status = 'executed'
                    artifacts[step.id] = artifact
                    row = {'id': step.id, 'handler': step.handler, 'status': status, 'cache_key': key}
                    history.append(row)
                    ACTIVE.get().complete(status)
                    atomic_json(work / 'progress.json', {'flow_id': task.flow_id, 'run_id': run_id, 'job_id': job, 'steps': history})
                    atomic_json(logs / 'progress.json', {'flow_id': task.flow_id, 'run_id': run_id, 'job_id': job, 'steps': history})
                current(runtime).attempt_id = None
                current(runtime).span_id = root_span
                current(runtime).parent_span_id = None
            terminal = artifacts[workflow.steps[-1].id]
            if current(runtime).cancel_requested.is_set():
                raise FlowError('RUN_CANCEL_REQUESTED', '已確認取消；停止成果發布')
            recorder.record('delivery.staged', attributes={'output_layout': layout})
            RunStore(runtime).transition(run_id, 'running', 'publishing')
            if getattr(workflow, 'outputs', {}):
                from .outputs import publish_named_outputs
                report = publish_named_outputs(stage, workflow, artifacts, layout)
            elif layout == 'categorized-v1' and hasattr(adapter, 'publish_outputs'):
                report = adapter.publish_outputs(terminal, stage, workflow)
            else:
                report = adapter.publish(terminal, artifact_directory(stage, layout), workflow)
            report['token_usage'] = ACTIVE.get().usage.finish()
            report['presentation'] = ACTIVE.get().references()
            if not getattr(workflow, 'outputs', {}):
                report.setdefault('compatibility_warnings', []).append('此流程沿用最後 Step 的 legacy 輸出映射。')
            report.update(flow_id=task.flow_id, run_id=run_id, job_id=job, runtime=__version__, steps=history, snapshot=snapshot, output=str(output), output_layout=layout)
            withheld=report.get("graph_delivery_status")=="withheld"
            if "graph_result_status" in report:
                report.update(result_status=report["graph_result_status"],delivery_status="withheld" if withheld else "published",
                              execution_status=report["blueprint"].get("execution_status",report["blueprint"]["status"]))
            if withheld:
                from .storage.delivery import (
                    commit_graph_preview,
                    delivery_root,
                    write_version_manifest,
                )
                preview_folder=delivery_root(output)/"previews"/run_id
                from .outputs import reject_links
                reject_links(preview_folder)
                report.update(output=str(preview_folder),requested_output=str(output))
            atomic_json(report_path, report)
            write_delivery_index(stage, report)
            recorder.record('delivery.verified', attributes={'status': 'verified'})
            atomic_json(logs / 'report.json', report)
            if withheld:
                write_version_manifest(stage,run_id)
                preview_folder.parent.mkdir(parents=True,exist_ok=True)
                stage.rename(preview_folder)
                commit_graph_preview(runtime,preview_folder,run_id)
            else:
                promote_staging(output, stage, run_id, store)
            return report
        except BaseException as exc:
            run_context = current(runtime)
            active_attempt = run_context.attempt_id if run_context else None
            diagnostic = record_exception(exc, logs, runtime=runtime, flow_id=task.flow_id, run_id=run_id, job_id=job,
                                          launch_id=launcher_id(runtime), step=ACTIVE.get().step_id)
            state = guard_secondary(exc, diagnostic, 'read_run_status', lambda: RunStore(runtime).get(run_id), runtime=runtime)
            if state and state['execution_status'] == 'publishing':
                with SecondaryOperation('ACTIVE.get.event_recorder.record'):
                    ACTIVE.get().event_recorder.record('delivery.failed', severity='ERROR',
                        attributes={'code': exc.code if isinstance(exc, FlowError) else type(exc).__name__})
            if active_attempt:
                with SecondaryOperation('ACTIVE.get.event_recorder.record'):
                    ACTIVE.get().event_recorder.record('step.failed', severity='ERROR',
                        attributes={'step_id': ACTIVE.get().step_id or 'unknown',
                                    'code': exc.code if isinstance(exc, FlowError) else type(exc).__name__})
            cancelled = isinstance(exc, FlowError) and exc.code in {'PROCESS_CANCELLED', 'RUN_CANCEL_REQUESTED'}
            guard_secondary(exc, diagnostic, 'finish_attempt_failure', lambda exc=exc: RunStore(runtime).finish_attempt_failure(active_attempt,
                interrupted=isinstance(exc, KeyboardInterrupt), cancelled=cancelled, error_id=diagnostic['error_id']), runtime=runtime)
            failure = {'job_id': job, 'status': 'cancelled' if cancelled else ('interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'),
                       'flow_id': task.flow_id, 'run_id': run_id, 'output_layout': layout,
                       'error': exc.code if isinstance(exc, FlowError) else type(exc).__name__, 'error_details': diagnostic, 'steps': history,
                       'token_usage': guard_secondary(exc, diagnostic, 'usage.finish', lambda: ACTIVE.get().usage.finish(), runtime=runtime),
                       'presentation': guard_secondary(exc, diagnostic, 'reporter.references', lambda: ACTIVE.get().references(), runtime=runtime)}
            if stage.exists():
                guard_secondary(exc, diagnostic, 'stage_failure', lambda: atomic_json(stage / 'failure.json', failure), runtime=runtime)
            guard_secondary(exc, diagnostic, 'run_report', lambda: atomic_json(logs / 'report.json', failure), runtime=runtime)
            raise
