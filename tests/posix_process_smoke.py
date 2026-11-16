"""Stdlib-only POSIX process smoke; can run without pytest in a Linux checkout."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from flow_base.execution.supervisor import process_started_at
from flow_base.runtime import FlowError, run_process


@unittest.skipUnless(os.name == 'posix', 'POSIX process semantics')
class ProcessSmoke(unittest.TestCase):
    def test_success_and_spawn_failure(self):
        self.assertEqual(run_process([sys.executable, '-c', 'print(42)']).strip(), '42')
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FlowError) as caught:
                run_process([str(Path(directory) / 'missing')], log=Path(directory) / 'missing.log')
            self.assertEqual(caught.exception.code, 'PROCESS_START')

    def test_nonzero_exit_has_worker_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FlowError) as caught:
                run_process([sys.executable, '-c', 'import sys;sys.exit(1)'],
                            log=Path(directory) / 'failure.log')
            self.assertEqual(caught.exception.code, 'PROCESS_FAILED')
            evidence = json.loads(Path(caught.exception.details['process_evidence']).read_text())
            self.assertEqual(evidence['returncode'], 1)
            self.assertTrue(evidence['worker_pid'])
            self.assertIsNone(evidence['oom_evidence'])

    def test_timeout_and_child_tree_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'tree.log'
            command = ('import subprocess,sys,time; '
                       'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]); '
                       'print(child.pid,flush=True);time.sleep(30)')
            with self.assertRaises(FlowError) as caught:
                run_process([sys.executable, '-c', command], timeout=0.5,
                            grace_seconds=0.2, log=log)
            self.assertEqual(caught.exception.code, 'PROCESS_TIMEOUT')
            pid = int(log.read_text().splitlines()[0])
            self.assertIsNone(process_started_at(pid))
            evidence = json.loads(Path(caught.exception.details['process_evidence']).read_text())
            self.assertTrue(evidence['posix_guardian'])
            self.assertNotEqual(evidence['worker_pid'], pid)
            self.assertIsNone(process_started_at(evidence['worker_pid']))
            self.assertIsInstance(evidence['peak_rss_bytes'], int)

    def test_cancel_and_child_tree_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'cancel.log'
            stop = threading.Event()
            timer = threading.Timer(0.5, stop.set)
            timer.start()
            command = ('import subprocess,sys,time; '
                       'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]); '
                       'print(child.pid,flush=True);time.sleep(30)')
            try:
                with self.assertRaises(FlowError) as caught:
                    run_process([sys.executable, '-c', command], timeout=10,
                                grace_seconds=0.2, cancel_event=stop, log=log)
            finally:
                timer.join(timeout=2)
            self.assertEqual(caught.exception.code, 'PROCESS_CANCELLED')
            pid = int(log.read_text().splitlines()[0])
            self.assertIsNone(process_started_at(pid))

    def test_worker_parent_exit_kills_child(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'parent.log'
            command = ('import subprocess,sys; '
                       'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]); '
                       'print(child.pid,flush=True)')
            self.assertEqual(run_process([sys.executable, '-c', command], log=log), '')
            pid = int(log.read_text().splitlines()[0])
            self.assertIsNone(process_started_at(pid))

    def test_abrupt_caller_death_kills_worker_group(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / 'identity.json'
            log = root / 'long.log'
            code = """
import json, sys, threading, time
from pathlib import Path
from flow_base.runtime import run_process
root, marker, log = map(Path, sys.argv[1:4])
worker_code = ('import subprocess,sys,time; '
               'child=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"]); '
               'print(child.pid,flush=True);time.sleep(30)')
threading.Thread(target=lambda: run_process([sys.executable, '-c', worker_code], log=log), daemon=True).start()
deadline = time.time() + 5
while time.time() < deadline and not marker.exists():
    for path in root.glob('proc_*.json'):
        try:
            data = json.loads(path.read_text())
            if data.get('worker_pid'):
                marker.write_text(json.dumps({'worker_pid': data['worker_pid'], 'guardian_pid': data['pid']}))
                break
        except Exception:
            pass
    time.sleep(0.02)
time.sleep(30)
"""
            parent = subprocess.Popen([sys.executable, '-c', code, str(root), str(marker), str(log)],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not marker.exists():
                    time.sleep(0.05)
                self.assertTrue(marker.exists(), parent.stderr.read().decode() if parent.poll() is not None else 'guardian did not start')
                identities = json.loads(marker.read_text())
                self.assertIsNotNone(process_started_at(identities['worker_pid']))
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not log.read_text().strip():
                    time.sleep(0.05)
                grandchild_pid = int(log.read_text().splitlines()[0])
                self.assertIsNotNone(process_started_at(grandchild_pid))
            finally:
                parent.kill()
                parent.wait(timeout=5)
                parent.stderr.close()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and process_started_at(identities['worker_pid']) is not None:
                time.sleep(0.05)
            self.assertIsNone(process_started_at(identities['worker_pid']))
            self.assertIsNone(process_started_at(grandchild_pid))


if __name__ == '__main__':
    unittest.main(verbosity=2)
