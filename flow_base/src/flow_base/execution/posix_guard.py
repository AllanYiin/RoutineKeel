"""Short-lived POSIX worker guardian; stdlib-only so it can run with Python -I.

The guardian is started only for one subprocess. If its caller disappears, it
terminates the worker's process group. It is not a server or persistent daemon.
"""
from __future__ import annotations

import ctypes
import os
import signal
import subprocess
import sys
import time


def _pdeathsig():
    if not sys.platform.startswith('linux'):
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(1, signal.SIGKILL, 0, 0, 0)  # PR_SET_PDEATHSIG
    except (OSError, AttributeError):
        pass


def main(argv):
    parent_pid, identity_fd = int(argv[0]), int(argv[1])
    command = argv[2:]
    if not command or os.getppid() != parent_pid:
        return 1
    guardian_pid = os.getpid()
    stopping = False
    worker = None

    def terminate(_signal, _frame):
        nonlocal stopping
        stopping = True
        if worker is not None and worker.poll() is None:
            try:
                os.killpg(worker.pid, signal.SIGTERM)
            except OSError:
                pass

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)

    def before_exec():
        _pdeathsig()
        if os.getppid() != guardian_pid:
            os._exit(1)

    try:
        try:
            worker = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                      stdout=sys.stdout.buffer, stderr=subprocess.STDOUT,
                                      start_new_session=True,
                                      preexec_fn=before_exec if sys.platform.startswith('linux') else None)  # noqa: PLW1509 -- isolated single-thread guardian sets the parent-death signal before exec
        except OSError as exc:
            os.write(identity_fd, f'ERR:{exc.errno or 0}'.encode('ascii'))
            return 127
        os.write(identity_fd, str(worker.pid).encode('ascii'))
    finally:
        os.close(identity_fd)
    while True:
        if os.getppid() != parent_pid:
            stopping = True
        if stopping:
            try:
                os.killpg(worker.pid, signal.SIGTERM)
            except OSError:
                pass
            time.sleep(0.05)
            try:
                os.killpg(worker.pid, signal.SIGKILL)
            except OSError:
                pass
            worker.wait()
            return 143
        returncode = worker.poll()
        if returncode is not None:
            try:
                os.killpg(worker.pid, signal.SIGKILL)
            except OSError:
                pass
            return returncode if returncode >= 0 else 128 - returncode
        time.sleep(0.05)


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
