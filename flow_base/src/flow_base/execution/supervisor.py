"""Heartbeat and process identity for synchronous local Runs."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from flow_base.observability.recovery import SecondaryOperation

from ..core.lifecycle import RunStore
from ..runtime import FlowError, atomic_json


def process_started_at(pid: int) -> float | None:
    """Return OS process birth time; unknown identity is never treated as alive."""
    if pid <= 0:
        return None
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            created = wintypes.FILETIME()
            exited = wintypes.FILETIME()
            kernel_time = wintypes.FILETIME()
            user_time = wintypes.FILETIME()
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                          ctypes.byref(kernel_time), ctypes.byref(user_time)):
                return None
            if exited.dwHighDateTime or exited.dwLowDateTime:
                return None
            ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
            return ticks / 10_000_000 - 11_644_473_600
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith('linux'):
        try:
            stat = Path(f'/proc/{pid}/stat').read_text(encoding='ascii')
            fields = stat[stat.rfind(') ')+2:].split()
            if fields[0] == 'Z':
                return None
            start_ticks = int(fields[19])
            with open('/proc/stat', encoding='ascii') as source:
                boot = next(int(line.split()[1]) for line in source if line.startswith('btime '))
            return boot + start_ticks / os.sysconf('SC_CLK_TCK')
        except (OSError, ValueError, StopIteration):
            return None
    if sys.platform == 'darwin':
        try:
            environment = dict(os.environ, LC_ALL='C')
            result = subprocess.run(['ps', '-p', str(pid), '-o', 'stat=', '-o', 'lstart='],
                                    capture_output=True, text=True, timeout=3,
                                    env=environment, check=False)
            if result.returncode != 0:
                return None
            state, _, started = result.stdout.strip().partition(' ')
            if not state or state.startswith('Z'):
                return None
            parts = started.split()
            if len(parts) != 5:
                return None
            _, month, day, clock, year = parts
            months = {name: index for index, name in enumerate(
                ('Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'), 1)}
            hours, minutes, seconds = (int(value) for value in clock.split(':'))
            return time.mktime((int(year), months[month], int(day),
                                hours, minutes, seconds, 0, 0, -1))
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            return None
    return None


def identity_matches(pid, started_at, token) -> bool:
    if not pid or not token or not started_at:
        return False
    actual = process_started_at(int(pid))
    return actual is not None and abs(actual - float(started_at)) < 1.0


class Supervisor:
    def __init__(self, runtime, run_id, context=None, *, interval=5, lease_seconds=20):
        self.store = RunStore(runtime)
        self.run_id = run_id
        self.context = context
        self.interval = interval
        self.lease_seconds = lease_seconds
        self.token = uuid.uuid4().hex
        self._stop = threading.Event()
        self._thread = None
        self.heartbeat_error = None

    def __enter__(self):
        sink = self.context.event_sink if self.context else None
        if sink:
            sink.record('run.claim_attempt')
        self.store.claim(self.run_id, token=self.token, pid=os.getpid(),
                         started_at=process_started_at(os.getpid()) or time.time(),
                         lease_seconds=self.lease_seconds)
        if sink:
            sink.record('run.claimed')
        self._thread = threading.Thread(target=self._heartbeat, name=f'run-heartbeat-{self.run_id}', daemon=True)
        self._thread.start()
        return self

    def _heartbeat(self):
        while not self._stop.wait(self.interval):
            with SecondaryOperation('renew supervisor lease') as heartbeat:
                self.store.heartbeat(self.run_id, self.token, lease_seconds=self.lease_seconds)
                if self.context and self.store.get(self.run_id)['execution_status'] == 'cancelling':
                    self.context.cancel_requested.set()
            if heartbeat.error is not None:
                exc = heartbeat.error
                self.heartbeat_error = exc
                return

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.interval + 1)


def start_detached(workspace, task, workflow, source, output=None, *, overrides=None,
                   offline=False, idempotency_key=None, spec_hash=None):
    """Reserve a Run, then launch an on-demand supervisor independent of the caller."""
    from ..engine import _execution_identity, reserve_workflow_run
    from ..outputs import choose_output

    runtime = workspace.runtime
    if workflow is None:
        if not spec_hash:
            raise FlowError('RUN_SPEC_INVALID', '獨立啟動需要已驗證的計畫指紋')
        output = choose_output(runtime, task, source, output)
        job_id, _, _ = _execution_identity(runtime, task, source, output)
        run_id = runtime.start_run(task.flow_id, source, output, job_id=job_id,
                                   spec_hash=spec_hash, idempotency_key=idempotency_key)
    else:
        run_id, output, spec_hash = reserve_workflow_run(runtime, task, workflow, source, output,
                                                        offline=offline, idempotency_key=idempotency_key)
    specification = {'schema_version': 1, 'task': task.manifest.id, 'source': str(Path(source).resolve()),
                     'output': str(Path(output).resolve()), 'overrides': overrides or {},
                     'offline': offline, 'spec_hash': spec_hash}
    folder = runtime.home / 'run_specs'
    folder.mkdir(parents=True, exist_ok=True)
    spec_file = folder / f'{run_id}.json'
    with runtime.lock('spawn:' + run_id, timeout=30):
        if spec_file.exists():
            if json.loads(spec_file.read_text(encoding='utf-8')) != specification:
                raise FlowError('IDEMPOTENCY_CONFLICT', '同一 Run 的啟動規格不一致', run_id=run_id)
            return {'run_id': run_id, 'status': RunStore(runtime).get(run_id)['execution_status']}
        atomic_json(spec_file, specification)
        logdir = runtime.logs / task.flow_id / run_id
        logdir.mkdir(parents=True, exist_ok=True)
        log = logdir / 'supervisor.log'
        flags = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name == 'nt' else 0
        kwargs = {'creationflags': flags} if os.name == 'nt' else {'start_new_session': True}
        try:
            with log.open('ab') as output_stream:
                child = subprocess.Popen([sys.executable, '-m', 'flow_base.execution.supervisor',
                                          '--workspace', str(workspace.root), '--run-id', run_id],
                                         stdin=subprocess.DEVNULL, stdout=output_stream, stderr=subprocess.STDOUT,
                                         cwd=workspace.root, close_fds=True, **kwargs)
        except OSError as exc:
            from ..diagnostics import record_exception
            from ..observability.diagnostics import guard_secondary
            primary = record_exception(exc, logdir, runtime=runtime, run_id=run_id)
            guard_secondary(exc, primary, 'finish_failure', lambda: RunStore(runtime).finish_failure(run_id), runtime=runtime)
            guard_secondary(exc, primary, 'update_run', lambda: runtime.update_run(run_id, 'failed', error='SUPERVISOR_START', finished=True), runtime=runtime)
            raise FlowError('SUPERVISOR_START', '無法啟動本機 Run supervisor', run_id=run_id) from exc
        RunStore(runtime).record_spawn(run_id,child.pid,process_started_at(child.pid))
        return {'run_id': run_id, 'status': 'created', 'supervisor_pid': child.pid}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args(argv)
    from ..diagnostics import record_exception
    from ..engine import run_workflow
    from ..workspace import Workspace

    workspace = Workspace(args.workspace)
    runtime = workspace.runtime
    spec_file = runtime.home / 'run_specs' / f'{args.run_id}.json'
    try:
        spec = json.loads(spec_file.read_text(encoding='utf-8'))
        if spec.get('schema_version') != 1:
            raise FlowError('RUN_SPEC_MISMATCH', '不支援的保留 Run 規格版本')
        task = workspace.task(spec['task'])
        workflow = task.configuration(runtime, spec['overrides'])
        result = run_workflow(runtime, task, workflow, Path(spec['source']), Path(spec['output']),
                              offline=spec['offline'], reserved_run_id=args.run_id)
        return 0 if result else 1
    except BaseException as exc:  # noqa: BLE001 -- the supervisor records the terminal run state for arbitrary adapter errors
        from ..observability.diagnostics import guard_secondary
        logdir = runtime.logs / 'supervisor' / args.run_id
        diagnostic = record_exception(exc, logdir, runtime=runtime, run_id=args.run_id)
        guard_secondary(exc, diagnostic, 'finish_failure', lambda exc=exc: RunStore(runtime).finish_failure(args.run_id,
            interrupted=isinstance(exc, KeyboardInterrupt), error_id=diagnostic['error_id']), runtime=runtime)
        guard_secondary(exc, diagnostic, 'update_run', lambda exc=exc: runtime.update_run(args.run_id,
            'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
            error=diagnostic['code'], finished=True), runtime=runtime)
        return 130 if isinstance(exc, KeyboardInterrupt) else 1


if __name__ == '__main__':
    raise SystemExit(main())
