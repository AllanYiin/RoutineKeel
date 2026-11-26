"""Subprocess environment and evidence helpers shared by Runtime's facade."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import queue
import re
import select
import signal
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock
from routinekeel_contracts.documa import DOCUMA_CONFIG

from flow_base.observability.recovery import SecondaryOperation

WINDOWS_ENV = frozenset({'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATHEXT', 'PATH',
                         'TEMP', 'TMP', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA',
                         'PROGRAMDATA', 'HOMEDRIVE', 'HOMEPATH',
                         'PROCESSOR_ARCHITECTURE', 'PROCESSOR_ARCHITEW6432'})
POSIX_ENV = frozenset({'HOME', 'PATH', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TMPDIR',
                       'XDG_CACHE_HOME', 'XDG_CONFIG_HOME'})
COMPUTE_ENV = frozenset({'CUDA_VISIBLE_DEVICES', 'NVIDIA_VISIBLE_DEVICES',
                         'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
                         'PYTORCH_CUDA_ALLOC_CONF', 'TORCH_HOME', 'HF_HOME',
                         'HF_HUB_CACHE', 'TRANSFORMERS_CACHE', 'SSL_CERT_FILE'})


def worker_environment(source=None):
    """Pass only operating-system and compute controls; never inherit credentials."""
    source = os.environ if source is None else source
    allowed = (WINDOWS_ENV if os.name == 'nt' else POSIX_ENV) | COMPUTE_ENV | DOCUMA_CONFIG
    result = {key: value for key, value in source.items()
              if key.upper() in allowed and isinstance(value, str)}
    result.update({'HF_HUB_OFFLINE': '1', 'TOKENIZERS_PARALLELISM': 'false',
                   'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8',
                   'PYTHONUNBUFFERED': '1'})
    return result


def _utc():
    return datetime.now(UTC).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _shape(argv):
    """Keep option names, hash values and paths; never persist raw command text."""
    shaped = []
    for item in argv:
        value = str(item)
        shaped.append(value if re.fullmatch(r'--?[A-Za-z][A-Za-z0-9_-]*', value)
                      else 'sha256:' + hashlib.sha256(value.encode()).hexdigest()[:16])
    return shaped


def _executable_fingerprint(value):
    path = Path(value)
    try:
        metadata = path.stat()
    except OSError:
        return None
    facts = f'{path.name}\0{metadata.st_size}\0{metadata.st_mtime_ns}'
    return 'metadata-sha256:' + hashlib.sha256(facts.encode()).hexdigest()


def _peak_rss(pid):
    if sys.platform.startswith('linux'):
        try:
            for line in Path(f'/proc/{pid}/status').read_text().splitlines():
                if line.startswith('VmHWM:'):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError):
            pass
    if sys.platform == 'win32':
        try:
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                            ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                            ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                            ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                            ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            psapi = ctypes.WinDLL('psapi', use_last_error=True)
            psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return None
            try:
                counters = Counters()
                counters.cb = ctypes.sizeof(counters)
                return int(counters.PeakWorkingSetSize) if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb) else None
            finally:
                kernel.CloseHandle(handle)
        except (OSError, AttributeError, ValueError):
            pass
    return None


class BoundedOutput:
    """Drain a merged pipe while keeping a bounded log and a small safe tail."""
    def __init__(self, log=None, *, max_file_bytes=4_000_000, max_segments=4, max_capture_bytes=1_000_000):
        if max_file_bytes < 1024 or max_segments < 1 or max_capture_bytes < 1024:
            raise ValueError('Process output limits are too small')
        self.log = Path(log) if log is not None else None
        self.max_file_bytes = max_file_bytes
        self.max_segments = max_segments
        self.max_capture_bytes = max_capture_bytes
        self.total_bytes = 0
        self.truncated = False
        self.tail = bytearray()
        self.memory = bytearray()
        self.pending = bytearray()
        self.reader_error = None
        self.progress_error = None
        self._log_disabled = False
        if self.log:
            self.log.parent.mkdir(parents=True, exist_ok=True)
            self.log.touch(exist_ok=True)

    def _segments(self):
        if self.log is None:
            return []
        return sorted(self.log.parent.glob(self.log.stem + '.[0-9][0-9][0-9][0-9][0-9][0-9]' + self.log.suffix))

    def _rotate(self):
        segments = self._segments()
        last = max((int(item.stem.rsplit('.', 1)[1]) for item in segments), default=0)
        rotated = self.log.with_name(f'{self.log.stem}.{last+1:06}{self.log.suffix}')
        os.replace(self.log, rotated)
        segments.append(rotated)
        while len(segments) > self.max_segments:
            segments.pop(0).unlink()
            self.truncated = True
        if self.truncated:
            marker = b'\n[older process output truncated]\n'
            self.log.write_bytes(marker)
            return len(marker)
        return 0

    def _write_log(self, block):
        if not block or self.log is None or self._log_disabled:
            return
        with FileLock(self.log.with_name(self.log.name + '.lock'), timeout=30):
            remaining = memoryview(block)
            while remaining:
                size = self.log.stat().st_size if self.log.exists() else 0
                if size >= self.max_file_bytes:
                    size = self._rotate()
                count = min(len(remaining), self.max_file_bytes - size)
                with self.log.open('ab') as output:
                    output.write(remaining[:count])
                remaining = remaining[count:]

    def feed(self, block, progress=None):
        self.total_bytes += len(block)
        self.tail = (self.tail + block)[-8000:]
        if self.log is not None:
            self._write_log(block)
        elif len(self.memory) < self.max_capture_bytes:
            count = self.max_capture_bytes - len(self.memory)
            self.memory.extend(block[:count])
            if len(block) > count:
                self.truncated = True
        else:
            self.truncated = True
        if progress is None:
            return
        self.pending.extend(block)
        while b'\n' in self.pending:
            line, _, rest = self.pending.partition(b'\n')
            self.pending = bytearray(rest)
            if len(line) > 16384:
                continue
            try:
                data = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if isinstance(data, dict) and data.get('event') == 'progress':
                with SecondaryOperation('progress', catch=BaseException) as progress_notification:
                    progress(data)
                if progress_notification.error is not None:
                    exc = progress_notification.error
                    self.progress_error = type(exc).__name__
        if len(self.pending) > 16384:
            self.pending.clear()

    def finish(self):
        if self.log is None:
            return
        from ..runtime import atomic_json
        atomic_json(self.log.with_name(self.log.name + '.manifest.json'),
                    {'schema_version': 1, 'current': self.log.name,
                     'segments': [item.name for item in self._segments()],
                     'total_bytes': self.total_bytes, 'truncated': self.truncated,
                     'stdout_stderr_merged': True})

    def text(self):
        raw = bytes(self.memory if self.log is None else b'')
        value = raw.decode('utf-8', errors='replace')
        return value + ('\n[process output truncated]\n' if self.truncated and self.log is None else '')

    def tail_text(self):
        return bytes(self.tail).decode('utf-8', errors='replace')


class WindowsJob:
    """Best-effort Job Object: close/terminate kills descendants on Windows."""
    def __init__(self, process):
        self.handle = None
        if os.name != 'nt':
            return
        with SecondaryOperation('open Windows job') as job_setup:
            import ctypes
            from ctypes import wintypes
            class BasicLimits(ctypes.Structure):
                _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64),
                            ('PerJobUserTimeLimit', ctypes.c_int64),
                            ('LimitFlags', wintypes.DWORD),
                            ('MinimumWorkingSetSize', ctypes.c_size_t),
                            ('MaximumWorkingSetSize', ctypes.c_size_t),
                            ('ActiveProcessLimit', wintypes.DWORD),
                            ('Affinity', ctypes.c_size_t),
                            ('PriorityClass', wintypes.DWORD),
                            ('SchedulingClass', wintypes.DWORD)]
            class IoCounters(ctypes.Structure):
                _fields_ = [('ReadOperationCount', ctypes.c_uint64),
                            ('WriteOperationCount', ctypes.c_uint64),
                            ('OtherOperationCount', ctypes.c_uint64),
                            ('ReadTransferCount', ctypes.c_uint64),
                            ('WriteTransferCount', ctypes.c_uint64),
                            ('OtherTransferCount', ctypes.c_uint64)]
            class ExtendedLimits(ctypes.Structure):
                _fields_ = [('BasicLimitInformation', BasicLimits),
                            ('IoInfo', IoCounters),
                            ('ProcessMemoryLimit', ctypes.c_size_t),
                            ('JobMemoryLimit', ctypes.c_size_t),
                            ('PeakProcessMemoryUsed', ctypes.c_size_t),
                            ('PeakJobMemoryUsed', ctypes.c_size_t)]
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            kernel.CreateJobObjectW.restype = wintypes.HANDLE
            kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
            kernel.SetInformationJobObject.restype = wintypes.BOOL
            kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            kernel.AssignProcessToJobObject.restype = wintypes.BOOL
            kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
            kernel.TerminateJobObject.restype = wintypes.BOOL
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel = kernel
            handle = kernel.CreateJobObjectW(None, None)
            if not handle:
                return
            limits = ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                kernel.CloseHandle(handle)
                return
            if not kernel.AssignProcessToJobObject(handle, int(process._handle)):
                kernel.CloseHandle(handle)
                return
            self.handle = handle
        if job_setup.error is not None:
            self.close()

    def terminate(self):
        if self.handle:
            return bool(self.kernel.TerminateJobObject(self.handle, 1))
        return False

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def _same_worker(pid, started_at):
    if not pid or started_at is None:
        return False
    from .supervisor import process_started_at
    actual = process_started_at(pid)
    return actual is not None and abs(actual - started_at) < 1.0


def _stop_tree(process, job, grace_seconds, worker_pid=None, worker_started_at=None):
    """Request a graceful stop, then force the whole process group or Job."""
    if os.name == 'nt':
        if process.poll() is None:
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
            except (OSError, ValueError):
                try:
                    process.terminate()
                except OSError:
                    pass
        try:
            process.wait(timeout=grace_seconds)
            phase = 'graceful'
        except subprocess.TimeoutExpired:
            phase = 'forced'
        if not job.terminate():
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        process.wait(timeout=5)
        return phase
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except OSError:
            try:
                process.terminate()
            except OSError:
                pass
    try:
        process.wait(timeout=grace_seconds)
        phase = 'graceful'
    except subprocess.TimeoutExpired:
        phase = 'forced'
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    if _same_worker(worker_pid, worker_started_at):
        try:
            os.killpg(worker_pid, signal.SIGKILL)
        except OSError:
            pass
    process.wait(timeout=5)
    return phase


def run_process(argv, *, timeout=60, cwd=None, env=None, log=None, progress=None,
                cancel_event=None, grace_seconds=10, max_log_bytes=4_000_000,
                max_log_segments=4):
    """Run without a shell, drain all output, and preserve bounded process evidence."""
    from ..core.run_context import current
    from ..runtime import FlowError, atomic_json, digest
    from .supervisor import process_started_at

    argv = [str(item) for item in argv]
    if not argv or timeout <= 0 or grace_seconds < 0:
        raise FlowError('PROCESS_ARGUMENTS', '無效的子程序命令或時間限制')
    context = current()
    sink = context.event_sink if context else None
    identifier = 'proc_' + uuid.uuid4().hex
    evidence_folder = context.logdir if context else (Path(log).parent if log is not None else None)
    evidence_path = evidence_folder / (identifier + '.json') if evidence_folder else None
    evidence = {'schema_version': 1, 'process_token': identifier,
                'executable_id': Path(argv[0]).name,
                'executable_fingerprint': _executable_fingerprint(argv[0]),
                'command_fingerprint': digest(argv), 'argv_shape': _shape(argv),
                'cwd_fingerprint': digest(str(cwd)) if cwd is not None else None,
                'timeout_seconds': timeout, 'grace_seconds': grace_seconds,
                'stdout_stderr_merged': True, 'platform': platform.system(),
                'started_at_utc': _utc(), 'pid': None, 'process_started_at': None,
                'returncode': None, 'peak_rss_bytes': None, 'oom_evidence': None,
                'status': 'spawn_attempt'}
    if sink:
        sink.record('process.spawn_attempt', attributes={'executable_id': evidence['executable_id'],
                                                          'command_fingerprint': evidence['command_fingerprint']})
    if evidence_path:
        try:
            atomic_json(evidence_path, evidence)
        except OSError as exc:
            raise FlowError('PROCESS_EVIDENCE', '程序證據無法於啟動前保存', process_token=identifier) from exc
    capture = BoundedOutput(log, max_file_bytes=max_log_bytes, max_segments=max_log_segments)
    guardian_read = guardian_write = None
    if os.name == 'nt':
        child_argv = argv
        kwargs = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        guardian_read, guardian_write = os.pipe()
        child_argv = [sys.executable, '-I', str(Path(__file__).with_name('posix_guard.py')),
                      str(os.getpid()), str(guardian_write), *argv]
        kwargs = {'start_new_session': True, 'pass_fds': (guardian_write,)}
    started = time.monotonic()
    try:
        process = subprocess.Popen(child_argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   close_fds=True, **kwargs)
    except OSError as exc:
        if guardian_read is not None:
            os.close(guardian_read)
            os.close(guardian_write)
        evidence.update(status='spawn_failed', error_type=type(exc).__name__, ended_at_utc=_utc(),
                        duration_ms=int((time.monotonic()-started)*1000))
        evidence_persisted = False
        if evidence_path:
            try:
                atomic_json(evidence_path, evidence)
                evidence_persisted = True
            except OSError:
                pass
        if sink:
            with SecondaryOperation('sink.record'):
                sink.record('process.spawn_failed', severity='ERROR', attributes={'code': 'PROCESS_START'})
        raise FlowError('PROCESS_START', str(exc), executable=argv[0],
                        process_evidence=str(evidence_path) if evidence_path else None,
                        evidence_persisted=evidence_persisted,
                        process_evidence_snapshot=evidence) from exc
    worker_pid = None
    if guardian_read is not None:
        os.close(guardian_write)
        try:
            ready, _, _ = select.select([guardian_read], [], [], 2)
            identity = os.read(guardian_read, 64) if ready else b''
            if not ready:
                _stop_tree(process, WindowsJob(process), 0)
                process.stdout.close()
                evidence.update(status='spawn_failed', error_type='GuardianHandshakeTimeout',
                                ended_at_utc=_utc(), returncode=process.returncode)
                if evidence_path:
                    try:
                        atomic_json(evidence_path, evidence)
                    except OSError:
                        pass
                raise FlowError('PROCESS_START', 'POSIX guardian did not report Worker identity',
                                executable=argv[0], process_evidence=str(evidence_path) if evidence_path else None,
                                process_evidence_snapshot=evidence)
            if identity.startswith(b'ERR:'):
                process.wait(timeout=5)
                process.stdout.close()
                evidence.update(status='spawn_failed', error_type='WorkerOSError', ended_at_utc=_utc(),
                                duration_ms=int((time.monotonic()-started)*1000), returncode=process.returncode)
                if evidence_path:
                    try:
                        atomic_json(evidence_path, evidence)
                    except OSError:
                        pass
                if sink:
                    with SecondaryOperation('sink.record'):
                        sink.record('process.spawn_failed', severity='ERROR', attributes={'code': 'PROCESS_START'})
                raise FlowError('PROCESS_START', 'Worker could not be started by the POSIX guardian',
                                executable=argv[0], process_evidence=str(evidence_path) if evidence_path else None,
                                process_evidence_snapshot=evidence)
            if identity.isdigit():
                worker_pid = int(identity)
            elif process.poll() is not None:
                process.stdout.close()
                if sink:
                    with SecondaryOperation('sink.record'):
                        sink.record('process.spawn_failed', severity='ERROR', attributes={'code': 'PROCESS_START'})
                raise FlowError('PROCESS_START', 'POSIX guardian ended before starting the Worker',
                                executable=argv[0], process_evidence_snapshot=evidence)
        except FlowError:
            raise
        except BaseException:
            _stop_tree(process, WindowsJob(process), 0)
            process.stdout.close()
            raise
        finally:
            os.close(guardian_read)
    job = WindowsJob(process)
    evidence.update(pid=process.pid, process_started_at=process_started_at(process.pid),
                    worker_pid=worker_pid, worker_started_at=process_started_at(worker_pid) if worker_pid else None,
                    windows_job_object=bool(job.handle),
                    posix_guardian=os.name != 'nt', status='running')
    if evidence_path:
        try:
            atomic_json(evidence_path, evidence)
        except OSError:
            pass
    if sink:
        try:
            sink.record('process.spawned', attributes={'child_pid': worker_pid or process.pid,
                                                       'process_started_at': evidence['worker_started_at'] if worker_pid else evidence['process_started_at']})
        except BaseException:
            _stop_tree(process, job, 0, worker_pid, evidence['worker_started_at'])
            job.close()
            raise

    progress_events = queue.SimpleQueue()

    def report_pending():
        if progress is None:
            return
        while True:
            try:
                item = progress_events.get_nowait()
            except queue.Empty:
                break
            with SecondaryOperation('progress', catch=BaseException) as progress_notification:
                progress(item)
            if progress_notification.error is not None:
                exc = progress_notification.error
                capture.progress_error = type(exc).__name__

    def drain():
        try:
            with SecondaryOperation('read worker output', catch=BaseException) as output_read:
                while True:
                    block = process.stdout.read1(65536)
                    if not block:
                        break
                    with SecondaryOperation('capture.feed', catch=BaseException) as feed_outcome:
                        capture.feed(block, progress_events.put if progress else None)
                    if feed_outcome.error is not None:
                        exc = feed_outcome.error
                        capture.reader_error = type(exc).__name__
                        capture._log_disabled = True
            if output_read.error is not None:
                exc = output_read.error
                capture.reader_error = type(exc).__name__
        finally:
            with SecondaryOperation('process.stdout.close', catch=BaseException) as close_outcome:
                process.stdout.close()
            if close_outcome.error is not None:
                exc = close_outcome.error
                capture.reader_error = capture.reader_error or type(exc).__name__

    reader = threading.Thread(target=drain, name='flow-base-process-output', daemon=True)
    reader.start()
    reason = None
    stop_phase = None
    peak = None
    try:
        deadline = started + timeout
        while process.poll() is None:
            report_pending()
            if cancel_event is not None and cancel_event.is_set():
                reason = 'cancelled'
                stop_phase = _stop_tree(process, job, grace_seconds, worker_pid, evidence['worker_started_at'])
                break
            if time.monotonic() >= deadline:
                reason = 'timeout'
                if sink:
                    with SecondaryOperation('sink.record'):
                        sink.record('process.timeout', severity='ERROR', attributes={'code': 'PROCESS_TIMEOUT'})
                stop_phase = _stop_tree(process, job, grace_seconds, worker_pid, evidence['worker_started_at'])
                break
            sample = _peak_rss(worker_pid or process.pid)
            peak = max(peak or 0, sample) if sample is not None else peak
            if progress is not None:
                with SecondaryOperation('progress', catch=BaseException) as heartbeat_notification:
                    progress({'event': 'heartbeat'})
                if heartbeat_notification.error is not None:
                    exc = heartbeat_notification.error
                    capture.progress_error = type(exc).__name__
            time.sleep(min(0.1, max(0.01, deadline-time.monotonic())))
        process.wait(timeout=5)
        if os.name != 'nt' and reason is None:
            # A worker may exit while a child remains in its new process group.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if _same_worker(worker_pid, evidence['worker_started_at']):
                try:
                    os.killpg(worker_pid, signal.SIGKILL)
                except OSError:
                    pass
        if os.name == 'nt' and reason is None and job.handle:
            job.close()  # kill descendants that outlived their worker parent
        reader.join(timeout=5)
        report_pending()
        if reader.is_alive():
            capture.reader_error = capture.reader_error or 'DrainTimeout'
        with SecondaryOperation('capture.finish', catch=BaseException) as finish_outcome:
            capture.finish()
        if finish_outcome.error is not None:
            exc = finish_outcome.error
            capture.reader_error = capture.reader_error or type(exc).__name__
        evidence.update(status=reason or ('succeeded' if process.returncode == 0 else 'failed'),
                        returncode=process.returncode, ended_at_utc=_utc(),
                        duration_ms=int((time.monotonic()-started)*1000),
                        peak_rss_bytes=peak, stop_phase=stop_phase,
                        output_bytes=capture.total_bytes, output_truncated=capture.truncated,
                        output_log=str(log) if log is not None else None,
                        output_manifest=str(Path(log).with_name(Path(log).name + '.manifest.json')) if log is not None else None,
                        output_reader_error=capture.reader_error,
                        progress_callback_error=capture.progress_error)
        evidence_persisted = False
        if evidence_path:
            with SecondaryOperation('write process evidence', catch=BaseException) as evidence_write:
                atomic_json(evidence_path, evidence)
                evidence_persisted = True
            if evidence_write.error is not None:
                exc = evidence_write.error
                evidence['evidence_write_error'] = type(exc).__name__
        if sink:
            with SecondaryOperation('sink.record', catch=BaseException) as record_outcome:
                if capture.truncated:
                    sink.record('process.output_truncated', severity='WARNING', attributes={'bytes': capture.total_bytes})
                sink.record('process.exited', attributes={'status': evidence['status'],
                                                          'exit_code': process.returncode,
                                                          'duration_ms': evidence['duration_ms'],
                                                          'merged': True, 'peak_rss_bytes': peak})
            if record_outcome.error is not None:
                event_error = record_outcome.error
                evidence['event_write_error'] = type(event_error).__name__
        details = {'exit_code': process.returncode, 'output': capture.tail_text(),
                   'log': str(log) if log is not None else None,
                   'process_evidence': str(evidence_path) if evidence_path else None,
                   'evidence_persisted': evidence_persisted,
                   'process_evidence_snapshot': evidence,
                   'output_truncated': capture.truncated, 'stdout_stderr_merged': True}
        if reason == 'cancelled':
            raise FlowError('PROCESS_CANCELLED', 'Worker 已停止，取消請求已確認', **details)
        if reason == 'timeout':
            raise FlowError('PROCESS_TIMEOUT', 'Process exceeded its time limit', timeout=timeout, **details)
        if process.returncode:
            raise FlowError('PROCESS_FAILED', 'Process returned a nonzero exit code',
                            executable=argv[0], **details)
        if capture.reader_error:
            raise FlowError('PROCESS_LOG_FAILED', 'Worker 輸出未能完整保存', **details)
        if evidence_path and not evidence_persisted:
            raise FlowError('PROCESS_EVIDENCE', '程序完成但退出證據無法保存', **details)
        return capture.text()
    finally:
        try:
            job.close()
        except OSError:
            pass
