"""Bounded process evidence for spawn failures, crashes, timeout and output flood."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from flow_base.execution.supervisor import process_started_at
from flow_base.runtime import FlowError, run_process


def _evidence(error):
    path = error.value.details['process_evidence']
    return json.loads(Path(path).read_text(encoding='utf-8'))


def test_spawn_failure_has_preoperation_evidence(tmp_path):
    log = tmp_path / 'missing.log'
    with pytest.raises(FlowError) as error:
        run_process([tmp_path / 'missing-worker.exe'], log=log)
    assert error.value.code == 'PROCESS_START'
    evidence = _evidence(error)
    assert evidence['status'] == 'spawn_failed'
    assert evidence['pid'] is None
    assert evidence['stdout_stderr_merged'] is True


def test_nonzero_exit_has_code_duration_and_bounded_tail(tmp_path):
    log = tmp_path / 'worker.log'
    with pytest.raises(FlowError) as error:
        run_process([sys.executable, '-c', 'import sys;print("known failure",flush=True);sys.exit(1)'], log=log)
    assert error.value.code == 'PROCESS_FAILED'
    assert error.value.details['exit_code'] == 1
    assert 'known failure' in error.value.details['output']
    evidence = _evidence(error)
    assert evidence['returncode'] == 1
    assert evidence['pid'] > 0 and evidence['process_started_at']
    assert evidence['duration_ms'] >= 0
    assert evidence['stdout_stderr_merged'] is True
    assert evidence['oom_evidence'] is None
    assert process_started_at(evidence['pid']) is None


def test_timeout_stops_process_and_records_phase(tmp_path):
    with pytest.raises(FlowError) as error:
        run_process([sys.executable, '-c', 'import time;time.sleep(30)'],
                    timeout=0.2, grace_seconds=0.1, log=tmp_path / 'timeout.log')
    assert error.value.code == 'PROCESS_TIMEOUT'
    evidence = _evidence(error)
    assert evidence['status'] == 'timeout'
    assert evidence['stop_phase'] in {'graceful', 'forced'}
    assert process_started_at(evidence['pid']) is None


def test_large_merged_output_rotates_and_marks_truncation(tmp_path):
    log = tmp_path / 'flood.log'
    code = 'import sys;sys.stdout.write("A"*12000);sys.stderr.write("B"*12000)'
    assert run_process([sys.executable, '-c', code], log=log,
                       max_log_bytes=1024, max_log_segments=2) == ''
    files = sorted(tmp_path.glob('flood*.log'))
    assert len(files) <= 3
    assert all(path.stat().st_size <= 1024 for path in files)
    manifest = json.loads((tmp_path / 'flood.log.manifest.json').read_text(encoding='utf-8'))
    assert manifest['total_bytes'] >= 24000
    assert manifest['truncated'] is True
    assert manifest['stdout_stderr_merged'] is True
    assert 'truncated' in log.read_text(encoding='utf-8', errors='replace')


def test_no_log_capture_is_bounded(tmp_path):
    output = run_process([sys.executable, '-c', 'print("x"*1500000)'])
    assert len(output) < 1_001_000
    assert '[process output truncated]' in output


def test_timeout_cleans_spawned_child_tree(tmp_path):
    log = tmp_path / 'tree.log'
    # This fixture needs only the standard library. Start CPython directly so the
    # original deadline exercises the spawned tree rather than venv/site initialization.
    executable = getattr(sys, '_base_executable', sys.executable)
    code = ('import subprocess,sys,time; '
            'child=subprocess.Popen([sys.executable,"-I","-S","-c","import time;time.sleep(30)"]); '
            'print(child.pid,flush=True); time.sleep(30)')
    with pytest.raises(FlowError) as error:
        run_process([executable, '-I', '-S', '-c', code], timeout=0.3, grace_seconds=0.1, log=log)
    assert error.value.code == 'PROCESS_TIMEOUT'
    child_pid = int(log.read_text(encoding='utf-8').splitlines()[0])
    assert process_started_at(child_pid) is None


def test_worker_parent_exit_does_not_leave_child(tmp_path):
    log = tmp_path / 'orphan.log'
    code = ('import subprocess,sys; '
            'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]); '
            'print(child.pid,flush=True)')
    assert run_process([sys.executable, '-c', code], log=log) == ''
    child_pid = int(log.read_text(encoding='utf-8').splitlines()[0])
    assert process_started_at(child_pid) is None


def test_cancel_cleans_spawned_child_tree(tmp_path):
    ready = tmp_path / 'child-spawned'

    class CancelWhenChildExists:
        def is_set(self):
            return ready.is_file()

    log = tmp_path / 'cancel-tree.log'
    code = ('import pathlib,subprocess,sys,time; '
            'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]); '
            'print(child.pid,flush=True); pathlib.Path(sys.argv[1]).touch(); time.sleep(30)')
    with pytest.raises(FlowError) as error:
        run_process([sys.executable, '-c', code, str(ready)], timeout=10, grace_seconds=0.1,
                    cancel_event=CancelWhenChildExists(), log=log)
    assert error.value.code == 'PROCESS_CANCELLED'
    child_pid = int(log.read_text(encoding='utf-8').splitlines()[0])
    assert process_started_at(child_pid) is None


def test_log_write_failure_does_not_hide_worker_exit(tmp_path, monkeypatch):
    from flow_base.execution.process import BoundedOutput
    def disk_failure(*args):
        raise OSError('read-only log')
    monkeypatch.setattr(BoundedOutput, '_write_log', disk_failure)
    with pytest.raises(FlowError) as error:
        run_process([sys.executable, '-c', 'import sys;print("output");sys.exit(1)'],
                    log=tmp_path / 'failed.log')
    assert error.value.code == 'PROCESS_FAILED'
    assert _evidence(error)['output_reader_error'] == 'OSError'


def test_no_stdout_pollution_when_capturing_worker(tmp_path, capsys):
    assert run_process([sys.executable, '-c', 'print("private worker output")'],
                       log=tmp_path / 'worker.log') == ''
    captured = capsys.readouterr()
    assert captured.out == '' and captured.err == ''
    assert 'private worker output' in (tmp_path / 'worker.log').read_text(encoding='utf-8')


def test_progress_callback_runs_on_owner_thread(tmp_path):
    owner = threading.get_ident()
    threads = []
    code = 'import json;print(json.dumps({"event":"progress","completed":1,"total":1}),flush=True)'
    run_process([sys.executable, '-c', code], log=tmp_path / 'progress.log',
                progress=lambda item: threads.append(threading.get_ident()))
    assert threads and set(threads) == {owner}


def test_process_evidence_omits_raw_argument_secrets(tmp_path):
    marker = 'SECRET_ARGUMENT_MARKER_456'
    code = f'private_value="{marker}";print("ok")'
    assert run_process([sys.executable, '-c', code], log=tmp_path / 'safe.log') == ''
    evidence = next(tmp_path.glob('proc_*.json')).read_text(encoding='utf-8')
    assert marker not in evidence
    assert 'sha256:' in evidence


def test_evidence_failure_prevents_spawn(tmp_path, monkeypatch):
    def fail_evidence(*args, **kwargs):
        raise OSError('evidence disk full')
    monkeypatch.setattr('flow_base.runtime.atomic_json', fail_evidence)
    monkeypatch.setattr('flow_base.execution.process.subprocess.Popen',
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('process must not start')))
    with pytest.raises(FlowError) as error:
        run_process([sys.executable, '-c', 'print(1)'], log=tmp_path / 'blocked.log')
    assert error.value.code == 'PROCESS_EVIDENCE'


@pytest.mark.parametrize('exit_code,expected', [(0, 'PROCESS_EVIDENCE'), (1, 'PROCESS_FAILED')])
def test_final_evidence_failure_preserves_process_outcome(tmp_path, monkeypatch, exit_code, expected):
    from flow_base import runtime
    real = runtime.atomic_json
    def fail_final(path, data):
        if Path(path).name.startswith('proc_') and data.get('status') in {'succeeded', 'failed'}:
            raise OSError('final evidence write failed')
        return real(path, data)
    monkeypatch.setattr(runtime, 'atomic_json', fail_final)
    with pytest.raises(FlowError) as error:
        run_process([sys.executable, '-c', f'import sys;sys.exit({exit_code})'],
                    log=tmp_path / 'worker.log')
    assert error.value.code == expected
    assert error.value.details['evidence_persisted'] is False
    assert error.value.details['process_evidence_snapshot']['returncode'] == exit_code


@pytest.mark.skipif(os.name != 'nt', reason='Windows Job Object parent-death behavior')
def test_abrupt_supervisor_death_closes_worker_job(tmp_path):
    ready = tmp_path / 'supervisor-ready'
    log = tmp_path / 'long-worker.log'
    code = """
import sys
from pathlib import Path
from flow_base.runtime import run_process
ready, log = map(Path, sys.argv[1:3])
ready.touch()
run_process([sys.executable, '-c', 'import time;time.sleep(30)'], log=log)
"""
    env = os.environ.copy()
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1] / 'flow_base/src')
    parent = subprocess.Popen([sys.executable, '-c', code, str(ready), str(log)],
                              env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        # Cold interpreter/import time precedes the worker startup deadline.
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not ready.exists() and parent.poll() is None:
            time.sleep(0.05)
        assert ready.exists(), 'supervisor did not initialize'
        deadline = time.monotonic() + 5
        worker_pid = None
        while time.monotonic() < deadline and worker_pid is None and parent.poll() is None:
            for record in tmp_path.glob('proc_*.json'):
                evidence = json.loads(record.read_text(encoding='utf-8'))
                if evidence.get('pid'):
                    assert evidence['windows_job_object']
                    worker_pid = evidence['pid']
            if worker_pid is None:
                time.sleep(0.05)
        assert worker_pid, parent.stderr.read().decode('utf-8', 'replace') if parent.poll() is not None else 'worker did not start'
        assert process_started_at(worker_pid) is not None
    finally:
        parent.kill()
        parent.wait(timeout=5)
        parent.stderr.close()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and process_started_at(worker_pid) is not None:
        time.sleep(0.05)
    assert process_started_at(worker_pid) is None
