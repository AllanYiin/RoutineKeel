"""Task-neutral execution, checkpointing, output ownership and content caches."""
from __future__ import annotations

import dataclasses
from pathlib import Path
from .presentation import Reporter, ACTIVE
from .core.run_context import RunContext, ACTIVE_RUN
from .core.lifecycle import RunStore
from .execution.supervisor import Supervisor
from .diagnostics import record_exception, launcher_id

from . import __version__
from .runtime import FlowError, Runtime, atomic_json, digest, read_json, require_space, sha256, event


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
    handlers = {}

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
            return {'workspace_relative': path.relative_to(runtime.home.parent).as_posix()}
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
    return digest({'revision': task.fingerprint, 'workflow': workflow.model_dump(),
                   'input_hash': input_hash, 'output': str(Path(output).resolve()),
                   'profile_key': profile_key, 'offline': offline})


def run_demo(runtime, task, output):
    from .outputs import choose_output, artifact_directory, validate_tree, write_delivery_index
    output = choose_output(runtime, task, requested=output)
    validate_tree(output)
    if output.exists() and any(output.iterdir()):
        raise FlowError('OUTPUT_EXISTS', 'Demo output must be empty')
    run_id = runtime.start_run(task.flow_id, 'demonstration_fixture', output)
    logs = runtime.logs / task.flow_id / run_id
    from types import SimpleNamespace
    reporter = Reporter(task.manifest.title + '｜示範資料（非正式處理）', task.flow_id, run_id, logs, 1)
    run_token = ACTIVE_RUN.set(RunContext(runtime, run_id, logs, reporter))
    reporter_token = ACTIVE.set(reporter)
    store = RunStore(runtime)
    supervisor = None
    try:
        store.transition(run_id, 'created', 'queued')
        supervisor = Supervisor(runtime, run_id, ACTIVE_RUN.get())
        supervisor.__enter__()
        store.transition(run_id, 'preparing', 'running')
        reporter.start()
        reporter.enter(SimpleNamespace(id='demo', handler='adapter.demo'), 1, '建立示範資料')
        result = task.load(runtime).demo(runtime, artifact_directory(output, task.manifest.output_layout))
        if supervisor.heartbeat_error:
            raise FlowError('SUPERVISOR_HEARTBEAT', '執行期間無法更新監督程序 lease') from supervisor.heartbeat_error
        if ACTIVE_RUN.get().cancel_requested.is_set():
            raise FlowError('RUN_CANCEL_REQUESTED', '已確認取消；停止成果發布')
        result.update(flow_id=task.flow_id, run_id=run_id, output=str(output), output_layout=task.manifest.output_layout)
        result['token_usage'] = reporter.usage.finish()
        result['presentation'] = reporter.references()
        atomic_json(output / 'report.json', result)
        write_delivery_index(output, result)
        atomic_json(logs / 'report.json', result)
        store.transition(run_id, 'running', 'publishing')
        delivery_index = output / 'deliverables.json'
        store.record_delivery(run_id, delivery_index if delivery_index.is_file() else output / 'report.json')
        store.transition(run_id, 'publishing', 'succeeded', result_status='demonstration', delivery_status='published')
        try:
            runtime.update_run(run_id, 'complete', finished=True)
            reporter.complete('executed')
            reporter.end(result, {'label': '示範結束：未執行正式處理，不能當作驗收證據。'})
        except Exception as post_error:
            try:
                result['post_delivery_diagnostic'] = record_exception(post_error, logs / 'post_delivery', run_id=run_id)['diagnostic_json']
            except Exception:
                result['post_delivery_diagnostic'] = 'unavailable'
        return result
    except BaseException as exc:
        cancelled = isinstance(exc, FlowError) and exc.code in {'PROCESS_CANCELLED', 'RUN_CANCEL_REQUESTED'}
        status = 'cancelled' if cancelled else ('interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed')
        code = exc.code if isinstance(exc, FlowError) else type(exc).__name__
        diagnostic = record_exception(exc, logs, flow_id=task.flow_id, run_id=run_id, launch_id=launcher_id(runtime))
        store.finish_failure(run_id, interrupted=isinstance(exc, KeyboardInterrupt), cancelled=cancelled, error_id=diagnostic['error_id'])
        status = store.get(run_id)['execution_status']
        reporter.fail(code, diagnostic['message'], interrupted=isinstance(exc, KeyboardInterrupt), diagnostic=diagnostic)
        runtime.update_run(run_id, status, error=code, finished=True)
        atomic_json(logs / 'failure.json', {'flow_id': task.flow_id, 'run_id': run_id, 'status': status, 'error': code, 'error_details': diagnostic,
                                                'token_usage': reporter.usage.finish(), 'presentation': reporter.references()})
        if isinstance(exc, FlowError):
            exc.details.update(flow_id=task.flow_id, run_id=run_id)
        raise
    finally:
        if supervisor is not None:
            supervisor.__exit__(None, None, None)
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
    reporter = Reporter(task.manifest.title, task.flow_id, run_id, logs, len(workflow.steps))
    run_token = ACTIVE_RUN.set(RunContext(runtime, run_id, logs, reporter))
    reporter_token = ACTIVE.set(reporter)
    store = RunStore(runtime)
    supervisor = None
    try:
        store.transition(run_id, 'created', 'queued')
        supervisor = Supervisor(runtime, run_id, ACTIVE_RUN.get())
        supervisor.__enter__()
        reporter.start()
        report = _run_workflow(runtime, task, workflow, source, output, run_id=run_id, offline=offline, prepared=prepared)
        if supervisor.heartbeat_error:
            raise FlowError('SUPERVISOR_HEARTBEAT', '執行期間無法更新監督程序 lease') from supervisor.heartbeat_error
        delivery_root = Path(report['output'])
        delivery_index = delivery_root / 'deliverables.json'
        store.record_delivery(run_id, delivery_index if delivery_index.is_file() else delivery_root / 'report.json')
        store.transition(run_id, 'publishing', 'succeeded',
                         result_status=str(report.get('status', 'unknown')).lower(), delivery_status='published')
        try:
            runtime.update_run(run_id, report.get('status', 'complete'), job_id=report.get('job_id'), finished=True)
            atomic_json(logs / 'run.json', {'flow_id': task.flow_id, 'run_id': run_id, 'job_id': report.get('job_id'), 'status': report.get('status', 'complete')})
            adapter = task.load(runtime)
            summary = adapter.result_summary(report) if hasattr(adapter, 'result_summary') else None
            reporter.end(report, summary)
        except Exception as post_error:
            try:
                report['post_delivery_diagnostic'] = record_exception(post_error, logs / 'post_delivery', run_id=run_id)['diagnostic_json']
            except Exception:
                report['post_delivery_diagnostic'] = 'unavailable'
        return report
    except BaseException as exc:
        cancelled = isinstance(exc, FlowError) and exc.code in {'PROCESS_CANCELLED', 'RUN_CANCEL_REQUESTED'}
        status = 'cancelled' if cancelled else ('interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed')
        code = exc.code if isinstance(exc, FlowError) else type(exc).__name__
        diagnostic = record_exception(exc, logs, flow_id=task.flow_id, run_id=run_id,
                                      launch_id=launcher_id(runtime), step=reporter.step_id)
        store.finish_failure(run_id, interrupted=isinstance(exc, KeyboardInterrupt), cancelled=cancelled, error_id=diagnostic['error_id'])
        status = store.get(run_id)['execution_status']
        reporter.fail(code, diagnostic['message'], interrupted=isinstance(exc, KeyboardInterrupt), diagnostic=diagnostic)
        runtime.update_run(run_id, status, error=code, finished=True)
        atomic_json(logs / 'run.json', {'flow_id': task.flow_id, 'run_id': run_id, 'task': task.manifest.id,
                                       'status': status, 'error': code})
        atomic_json(logs / 'failure.json', {'flow_id': task.flow_id, 'run_id': run_id, 'status': status, 'error': code, 'error_details': diagnostic,
                                                'token_usage': reporter.usage.finish(), 'presentation': reporter.references()})
        if isinstance(exc, FlowError):
            exc.details.update(flow_id=task.flow_id, run_id=run_id)
        raise
    finally:
        if supervisor is not None:
            supervisor.__exit__(None, None, None)
        ACTIVE.reset(reporter_token)
        ACTIVE_RUN.reset(run_token)


def _run_workflow(runtime, task, workflow, source, output, *, run_id, offline=False, prepared=None):
    from .outputs import output_layout, artifact_directory, validate_tree, write_delivery_index
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
        validate_tree(output)
        report_path = output / 'report.json'
        layout = output_layout(output, task.manifest.output_layout)
        if output.exists() and (not output.is_dir() or any(output.iterdir())):
            if not report_path.is_file() or read_json(report_path).get('job_id') != job:
                raise FlowError('OUTPUT_EXISTS', 'Output contains another job; choose an empty directory')
        work.mkdir(parents=True, exist_ok=True)
        logs.mkdir(parents=True, exist_ok=True)
        if hasattr(adapter, 'check_output'):
            adapter.check_output(runtime, workflow, artifact_directory(output, layout))
        output.mkdir(parents=True, exist_ok=True)
        history = []
        atomic_json(report_path, {'job_id': job, 'status': 'preparing', 'runtime': __version__, 'output_layout': layout})
        try:
            runtime.update_run(run_id, 'preparing', job_id=job)
            if prepared is None:
                adapter.prepare(runtime, workflow, offline)
                adapter.inspect_input(runtime, workflow, source)
                prepared = adapter.verify(runtime, workflow)
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
            artifacts = {}
            for index, step in enumerate(workflow.steps, 1):
                if current(runtime).cancel_requested.is_set():
                    raise FlowError('RUN_CANCEL_REQUESTED', '已確認取消；停止後續節點')
                current(runtime).attempt_id = store.create_attempt(run_id, step.id)
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
                store.set_attempt_cache_key(current(runtime).attempt_id, key)
                with runtime.lock('step:' + key, timeout=600):
                    artifact = runtime.cached(key)
                    if artifact and hasattr(adapter, 'artifact_valid') and not adapter.artifact_valid(runtime, step.handler, artifact):
                        artifact = None
                    if artifact:
                        status = 'cached'
                        store.transition_attempt(current(runtime).attempt_id, 'pending', 'cached')
                    else:
                        store.transition_attempt(current(runtime).attempt_id, 'pending', 'running')
                        target = runtime.home / 'artifacts' / key / 'result.json'
                        target.parent.mkdir(parents=True, exist_ok=True)
                        atomic_json(logs / 'pending_step.json', {'flow_id': task.flow_id, 'run_id': run_id,
                            'job_id': job, 'step': step.id, 'handler': step.handler,
                            'artifact': target.relative_to(runtime.home).as_posix()})
                        require_space(target, adapter.minimum_space(workflow))
                        artifact = Path(adapter.handlers[step.handler](ctx, step, deps, target)).resolve()
                        if not artifact.is_relative_to(target.parent.resolve()) or not artifact.is_file():
                            raise FlowError('HANDLER_CONTRACT', 'Handler must return a file in its assigned artifact directory')
                        runtime.remember(key, artifact)
                        store.transition_attempt(current(runtime).attempt_id, 'running', 'succeeded')
                        status = 'executed'
                    artifacts[step.id] = artifact
                    row = {'id': step.id, 'handler': step.handler, 'status': status, 'cache_key': key}
                    history.append(row)
                    ACTIVE.get().complete(status)
                    atomic_json(work / 'progress.json', {'flow_id': task.flow_id, 'run_id': run_id, 'job_id': job, 'steps': history})
                    atomic_json(logs / 'progress.json', {'flow_id': task.flow_id, 'run_id': run_id, 'job_id': job, 'steps': history})
                current(runtime).attempt_id = None
            terminal = artifacts[workflow.steps[-1].id]
            if current(runtime).cancel_requested.is_set():
                raise FlowError('RUN_CANCEL_REQUESTED', '已確認取消；停止成果發布')
            RunStore(runtime).transition(run_id, 'running', 'publishing')
            if layout == 'categorized-v1' and hasattr(adapter, 'publish_outputs'):
                report = adapter.publish_outputs(terminal, output, workflow)
            else:
                report = adapter.publish(terminal, artifact_directory(output, layout), workflow)
            report['token_usage'] = ACTIVE.get().usage.finish()
            report['presentation'] = ACTIVE.get().references()
            report.update(flow_id=task.flow_id, run_id=run_id, job_id=job, runtime=__version__, steps=history, snapshot=snapshot, output=str(output), output_layout=layout)
            atomic_json(report_path, report)
            write_delivery_index(output, report)
            atomic_json(logs / 'report.json', report)
            return report
        except BaseException as exc:
            diagnostic = record_exception(exc, logs, flow_id=task.flow_id, run_id=run_id, job_id=job,
                                          launch_id=launcher_id(runtime), step=ACTIVE.get().step_id)
            cancelled = isinstance(exc, FlowError) and exc.code in {'PROCESS_CANCELLED', 'RUN_CANCEL_REQUESTED'}
            RunStore(runtime).finish_attempt_failure(current(runtime).attempt_id,
                interrupted=isinstance(exc, KeyboardInterrupt), cancelled=cancelled, error_id=diagnostic['error_id'])
            failure = {'job_id': job, 'status': 'cancelled' if cancelled else ('interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed'),
                       'flow_id': task.flow_id, 'run_id': run_id, 'output_layout': layout,
                       'error': exc.code if isinstance(exc, FlowError) else type(exc).__name__, 'error_details': diagnostic, 'steps': history,
                       'token_usage': ACTIVE.get().usage.finish(), 'presentation': ACTIVE.get().references()}
            atomic_json(report_path, failure)
            atomic_json(logs / 'report.json', failure)
            raise
