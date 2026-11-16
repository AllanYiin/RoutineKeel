"""Shared runtime infrastructure. Does not import any speech/ML package."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import closing, contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from . import __version__


class FlowError(Exception):
    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code, self.details = code, details

    def as_dict(self):
        return {"ok": False, "error": {"code": self.code, "message": str(self), "details": self.details}}


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def atomic_json(path: Path, value):
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode())


def atomic_bytes(path: Path, value: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".writing-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def atomic_copy(source: Path, target: Path):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".copy-", dir=target.parent)
    try:
        with source.open("rb") as src, os.fdopen(fd, "wb") as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def event(name, **data):
    from .presentation import render_event
    render_event(name, data)


def default_home() -> Path:
    override = os.environ.get("FLOW_BASE_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "flow_base"
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/flow_base"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "flow_base"


def existing_parent(path: Path):
    while not path.exists():
        path = path.parent
    return path


def free_bytes(path: Path):
    return shutil.disk_usage(existing_parent(path)).free


def memory_info():
    """Best-effort observation. Unknown is never reported as zero RAM."""
    try:
        if sys.platform.startswith("linux"):
            fields = {}
            for line in Path("/proc/meminfo").read_text().splitlines():
                key, value = line.split(":", 1)
                fields[key] = int(value.split()[0]) * 1024
            return {"total": fields["MemTotal"], "available": fields.get("MemAvailable"), "source": "proc"}
        if os.name == "nt":
            import ctypes
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [(n, ctypes.c_ulonglong) for n in
                    ["total", "available", "total_page", "available_page", "total_virtual", "available_virtual", "extended"]]
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return {"total": status.total, "available": status.available, "source": "GlobalMemoryStatusEx"}
        if sys.platform == "darwin":
            total = int(run_process(["sysctl", "-n", "hw.memsize"], timeout=5).strip())
            return {"total": total, "available": None, "source": "sysctl"}
    except (OSError, ValueError, FlowError):
        pass
    return {"total": None, "available": None, "source": "unknown"}


def require_space(path: Path, required: int):
    available = free_bytes(path)
    if available < required:
        raise FlowError("DISK_SPACE", "Not enough free disk space", required=required, available=available, path=str(path))


def log_tail(log, limit=8000):
    if log is None:
        return ''
    try:
        with Path(log).open('rb') as source:
            source.seek(0, 2)
            source.seek(max(0, source.tell() - limit))
            return source.read(limit).decode('utf-8', errors='replace')
    except OSError:
        return ''


def run_process(argv, *, timeout=60, cwd=None, env=None, log: Path | None = None, progress=None):
    """No shell evaluation. Bounded subprocesses, with process-tree termination."""
    kwargs = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
    offset = log.stat().st_size if log and log.exists() else 0
    pending = b""
    stream = log.open("ab") if log else subprocess.PIPE
    try:
        p = subprocess.Popen([str(x) for x in argv], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                             stdout=stream, stderr=subprocess.STDOUT, **kwargs)
    except OSError as e:
        if log:
            stream.close()
        raise FlowError("PROCESS_START", str(e), executable=str(argv[0])) from e
    def poll_progress():
        nonlocal offset, pending
        if not log or progress is None: return
        with log.open('rb') as reader:
            reader.seek(offset)
            block = reader.read(65536)
            offset = reader.tell()
        pending += block
        while b'\n' in pending:
            line, pending = pending.split(b'\n',1)
            if len(line) > 16384: continue
            try:
                data = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if isinstance(data,dict) and data.get('event') == 'progress': progress(data)
        if len(pending) > 16384: pending = b''
        progress({'event':'heartbeat'})
    try:
        if progress is None or not log:
            out, _ = p.communicate(timeout=timeout)
        else:
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline-time.monotonic()
                if remaining <= 0: raise subprocess.TimeoutExpired(argv,timeout)
                try:
                    out, _ = p.communicate(timeout=min(0.5,remaining))
                    poll_progress()
                    break
                except subprocess.TimeoutExpired:
                    poll_progress()
    except BaseException as e:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        p.wait()
        if not isinstance(e, subprocess.TimeoutExpired):
            raise
        raise FlowError("PROCESS_TIMEOUT", "Process exceeded its time limit", timeout=timeout,
                        output=log_tail(log), log=str(log) if log else None) from e
    finally:
        if log:
            stream.close()
    text = out.decode("utf-8", errors="replace") if out else ""
    if p.returncode:
        raise FlowError("PROCESS_FAILED", "Process returned a nonzero exit code", exit_code=p.returncode,
                        executable=str(argv[0]), output=text[-8000:] if text else log_tail(log), log=str(log) if log else None)
    return text


def inspect_environment(home: Path, output: Path | None = None):
    from .hardware import inspect_gpu
    gpu = inspect_gpu(run_process)
    return {"schema_version": 1, "runtime_version": __version__, "os": platform.system(), "machine": platform.machine(),
            "python": {"executable": sys.executable, "version": platform.python_version(), "bits": 64 if sys.maxsize > 2**32 else 32},
            "uv": shutil.which("uv"), "gpu": gpu, "memory": memory_info(), "home": str(home),
            "free_bytes": {"home": free_bytes(home), "output": free_bytes(output or Path.cwd())},
            "profile_support": "Declared by each installed workflow"}


class Runtime:
    def __init__(self, home: Path | None = None):
        self.home = (home or default_home()).expanduser().resolve()
        self.external_profiles = {}
        self.workers = {}
        self.logs = self.home.parent / "share_logs"
        self.secret_names = {"FLOW_BASE_API_KEY"}

    def record_token_usage(self, request_id, usage=None, *, pending=False):
        """Adapters register provider receipts without sharing prompt/response text."""
        from .presentation import ACTIVE
        reporter = ACTIVE.get()
        if reporter is None:
            raise FlowError('RUN_CONTEXT_REQUIRED', 'Token receipts require an active workflow run')
        if not isinstance(request_id, str) or not request_id:
            raise FlowError('USAGE_REQUEST_ID', 'A stable request ID is required')
        reporter.token_receipt(request_id, usage, pending=pending)

    def initialize(self):
        self.home.mkdir(parents=True, exist_ok=True)
        # Journal mode belongs to the database. Serialize the initial transition
        # across processes; ordinary connections must not try to change it.
        with FileLock(self.home / 'state.initialize.lock', timeout=30):
            database = self.home / 'state.sqlite3'
            existed = database.exists()
            with closing(self.connect()) as db:
                from .storage.migrations import migrate
                migrate(db, database, existed=existed)
                if db.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                    db.execute("PRAGMA journal_mode=WAL")

    def connect(self):
        db = sqlite3.connect(self.home / "state.sqlite3", timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def register_flow(self, task_id, version, fingerprint, title):
        self.initialize()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT flow_id FROM flows WHERE task_id=?', (task_id,)).fetchone()
            now = time.time()
            if row:
                flow_id = row['flow_id']
                db.execute('UPDATE flows SET version=?,fingerprint=?,title=?,updated=? WHERE flow_id=?',
                           (version, fingerprint, title, now, flow_id))
            else:
                flow_id = 'flow_' + uuid.uuid4().hex
                db.execute('INSERT INTO flows VALUES(?,?,?,?,?,?,?)',
                           (flow_id, task_id, version, fingerprint, title, now, now))
        return flow_id

    def start_run(self, flow_id, source, output):
        run_id = 'run_' + uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)',
                       (run_id, flow_id, None, 'starting', str(source), str(output), time.time(), None, None))
        return run_id

    def update_run(self, run_id, status, *, job_id=None, error=None, finished=False):
        with self.connect() as db:
            db.execute('UPDATE runs SET status=?,job_id=COALESCE(?,job_id),error=?,finished=? WHERE run_id=?',
                       (status, job_id, error, time.time() if finished else None, run_id))

    def history(self, flow_id=None, limit=30):
        self.initialize()
        with self.connect() as db:
            if flow_id:
                rows = db.execute('SELECT * FROM runs WHERE flow_id=? ORDER BY started DESC LIMIT ?', (flow_id, limit))
            else:
                rows = db.execute('SELECT * FROM runs ORDER BY started DESC LIMIT ?', (limit,))
            return [dict(row) for row in rows]

    @contextmanager
    def lock(self, key, timeout=1):
        self.initialize()
        path = self.home / "locks" / (digest(key) + ".lock")
        path.parent.mkdir(exist_ok=True)
        try:
            with FileLock(path, timeout=timeout):
                yield
        except Timeout as e:
            raise FlowError("BUSY", "Another process holds this resource/workflow lock", key=str(key)) from e

    def cached(self, key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM steps WHERE cache_key=? AND status='done'", (key,)).fetchone()
        if row:
            path = self.home / row["artifact"]
            if path.is_file() and sha256(path) == row["artifact_hash"]:
                return path
        return None

    def remember(self, key, artifact):
        relative = artifact.resolve().relative_to(self.home).as_posix()
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO steps VALUES(?,?,?,?,?)",
                       (key, "done", relative, sha256(artifact), time.time()))

    def profile_source(self, profile):
        if profile in self.external_profiles:
            return self.external_profiles[profile]
        raise FlowError("PROFILE_MISSING", "Workflow has not registered this profile", profile=profile)

    def register_profile(self, name, project):
        if name == "core":
            raise FlowError("PROFILE_COLLISION", "Cannot replace a built-in profile")
        source = Path(project).resolve()
        for file in ("pyproject.toml", "uv.lock", ".python-version"):
            if not (source / file).is_file():
                raise FlowError("PROFILE_INVALID", "Custom profile requires a locked uv project", missing=file)
        version = (source / ".python-version").read_text().strip()
        import tomllib
        project_data = tomllib.loads((source / "pyproject.toml").read_text())
        uv_config = project_data.get("tool", {}).get("uv", {})
        if uv_config.get("package") is not False or "workspace" in uv_config:
            raise FlowError("PROFILE_INVALID", "Custom profile must be a standalone dependency project with tool.uv.package=false")
        import re
        if not re.fullmatch(r"3\.(?:11|12|13)(?:\.\d+)?", version):
            raise FlowError("PROFILE_INVALID", "Custom profile must pin Python 3.11, 3.12 or 3.13")
        self.external_profiles[name] = source

    def profile_key(self, profile):
        source = self.profile_source(profile)
        return digest({"lock": sha256(source / "uv.lock"),
                       "project": sha256(source / "pyproject.toml"),
                       "os": platform.system(), "arch": platform.machine(),
                       "python": (source / ".python-version").read_text().strip()})[:24]

    def environment(self, profile):
        root = self.home / "environments" / self.profile_key(profile)
        python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        return root, python

    def prepare_profile(self, profile, offline=False):
        if profile == "core":
            return Path(sys.executable)
        source = self.profile_source(profile)
        root, python = self.environment(profile)
        with self.lock("env:" + self.profile_key(profile), timeout=600):
            root.mkdir(parents=True, exist_ok=True)
            location = root / "location.json"
            current = {"path": str(root)}
            if (root / ".venv").exists() and (not location.exists() or read_json(location) != current):
                shutil.rmtree(root / ".venv")
            for name in ("pyproject.toml", "uv.lock", ".python-version"):
                shutil.copyfile(source / name, root / name)
            uv = shutil.which("uv")
            if not uv:
                raise FlowError("UV_MISSING", "Install uv with the included launcher, then retry")
            env = os.environ.copy()
            env.update({"UV_CACHE_DIR": str(self.home / "packages"), "UV_PYTHON_INSTALL_DIR": str(self.home / "python"),
                        "UV_PROJECT_ENVIRONMENT": str(root / ".venv"), "UV_NO_PROGRESS": "1"})
            version = (source / ".python-version").read_text().strip()
            argv = [uv, "sync", "--locked", "--no-dev", "--no-editable", "--python", version, "--no-build"]
            if offline:
                argv += ["--offline"]
            from .presentation import ACTIVE
            reporter = ACTIVE.get()
            run_process(argv, cwd=root, env=env, timeout=1800, log=self.logs / "install" / (self.profile_key(profile) + ".log"),
                        progress=reporter.progress if reporter else None)
            if not python.is_file():
                raise FlowError("ENV_INVALID", "Profile Python was not created")
            atomic_json(location, current)
        return python

    def worker(self, profile, request, *, timeout=60, log=None):
        _, python = self.environment(profile)
        if not python.is_file():
            raise FlowError("ENV_MISSING", "Run prepare first", profile=profile)
        work = self.home / "requests"
        work.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(suffix=".json", dir=work)
        os.close(fd)
        path = Path(name)
        response = path.with_suffix(".response.json")
        try:
            atomic_json(path, {**request, "response_path": str(response)})
            env = os.environ.copy()
            env.update({"HF_HUB_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false",
                        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
            # Worker never receives credentials belonging to the LLM service.
            for secret in self.secret_names:
                env.pop(secret, None)
            worker_log = log or (getattr(self, 'run_logdir', None) or self.logs / "workers") / (path.stem + ".log")
            from .presentation import ACTIVE
            reporter = ACTIVE.get()
            run_process([python, self.workers[profile], path], timeout=timeout, env=env, log=worker_log,
                        progress=reporter.progress if reporter else None)
            return read_json(response)
        finally:
            path.unlink(missing_ok=True)
            response.unlink(missing_ok=True)
