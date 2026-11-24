"""Install the packaged Studio into the workspace Runtime environment."""

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath
from uuid import uuid4

from filelock import FileLock


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def owned(root, path):
    """Reject linked paths before creating, executing or moving an owned directory."""
    if not path.absolute().is_relative_to(root):
        raise ValueError("Path is outside the Studio installation")
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("Linked installation paths are not supported")
    if not path.resolve().is_relative_to(root):
        raise ValueError("Resolved path is outside the Studio installation")
    return path


def verify_bundle(root):
    bundle = json.loads(owned(root, root / "bundle.json").read_text(encoding="utf-8"))
    if bundle["schema_version"] != 1 or not isinstance(bundle["files"], dict):
        raise ValueError("Unsupported Studio bundle manifest")
    for name, expected in bundle["files"].items():
        relative = PurePosixPath(name)
        if (
            not name
            or relative.is_absolute()
            or ".." in relative.parts
            or "\\" in name
            or ":" in name
        ):
            raise ValueError("Invalid bundle path")
        path = owned(root, root / name)
        if not path.is_file() or digest(path) != expected:
            raise ValueError("Studio package integrity check failed: " + name)
    for name in (
        bundle["studio_wheel"],
        bundle["contracts_wheel"],
        "studio-requirements.txt",
        "studio_bootstrap.py",
    ):
        if name not in bundle["files"]:
            raise ValueError("Required installer file is not covered by the manifest")
    return bundle


def clean_environment(runtime_state):
    names = {
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATH",
        "PATHEXT",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "LOCALAPPDATA",
        "LANG",
        "LC_ALL",
        "UV_OFFLINE",
    }
    env = {k: v for k, v in os.environ.items() if k.upper() in names}
    env.update(
        UV_CACHE_DIR=str(runtime_state / "packages"),
        UV_PYTHON_DOWNLOADS="never",
        UV_NO_ENV_FILE="1",
        UV_NO_CONFIG="1",
        UV_NO_MODIFY_PATH="1",
        UV_LINK_MODE="copy",
        UV_NO_PROGRESS="1",
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
    )
    return env


def find_uv(workspace, runtime_state):
    system = shutil.which("uv")
    if system:
        return system
    suffix = "uv.exe" if os.name == "nt" else "uv"
    architecture = (
        os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE")
        if os.name == "nt"
        else platform.machine()
    )
    candidate = owned(
        workspace, runtime_state / f"bootstrap/{platform.system()}-{architecture}/{suffix}"
    )
    if not candidate.is_file():
        raise ValueError("Run the packaged launcher to prepare uv and Runtime first")
    return str(candidate)


def check_environment(python, environment, version, *, runtime=False):
    if not python.is_file():
        return False
    module = "flow_base" if runtime else "workflow_studio"
    imports = "import flow_base.core.service" if runtime else "import workflow_studio.service"
    script = (
        "import pathlib,sys; "
        f"import {module}; {imports}; "
        "assert sys.version_info[:2] == (3,12); "
        "assert pathlib.Path(sys.prefix).resolve() == pathlib.Path(sys.argv[1]).resolve(); "
        f"assert {module}.__version__ == sys.argv[2]; "
        f"assert pathlib.Path({module}.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve())"
    )
    try:
        result = subprocess.run(
            [str(python), "-X", "utf8", "-I", "-c", script, str(environment), version],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def prepare(root, bundle, workspace):
    executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    runtime_env = owned(workspace, workspace / ".venv")
    python = runtime_env / executable
    # POSIX venv Python is intentionally a symlink to the base interpreter.
    # Validate the containing directories and the launched sys.prefix instead.
    owned(workspace, python.parent)
    runtime_dir = root / "runtime" if workspace == root else workspace / "flow_base"
    runtime_state = owned(
        workspace,
        (runtime_dir if (workspace / "workflows").is_dir() else workspace) / "share_states",
    )
    state = owned(root, root / "share_states")
    state.mkdir(exist_ok=True)
    marker = owned(root, state / "studio-environment.json")
    expected = {
        "wheel": bundle["files"][bundle["studio_wheel"]],
        "contracts": bundle["files"][bundle["contracts_wheel"]],
        "requirements": bundle["files"]["studio-requirements.txt"],
        "root": str(root),
        "environment": str(runtime_env),
        "platform": platform.system(),
        "machine": platform.machine(),
    }
    lock = owned(root, state / "studio-bootstrap.lock")
    with FileLock(str(lock), timeout=120):
        if not check_environment(python, runtime_env, bundle["runtime_version"], runtime=True):
            raise ValueError("底座工作區的 .venv 缺失或版本不符，請先執行同次發行的底座啟動器。")
        if marker.is_file():
            try:
                valid_marker = json.loads(marker.read_text(encoding="utf-8")) == expected
            except (OSError, ValueError):
                valid_marker = False
            if valid_marker and check_environment(python, runtime_env, bundle["studio_version"]):
                return python
        uv = find_uv(workspace, runtime_state)
        runtime_lock = owned(workspace, runtime_state / "bootstrap.lock")
        try:
            runtime_lock.mkdir()
        except FileExistsError as exc:
            raise ValueError("底座正在準備共用環境。請等啟動器結束後再開啟 Studio。") from exc
        try:
            if not check_environment(python, runtime_env, bundle["runtime_version"], runtime=True):
                raise ValueError("底座環境已變更，請重新啟動 Studio。")
            env = clean_environment(runtime_state)
            commands = [
                [
                    uv,
                    "pip",
                    "install",
                    "--no-config",
                    "--python",
                    str(python),
                    "--require-hashes",
                    "--no-build",
                    "--no-deps",
                    "-r",
                    str(root / "studio-requirements.txt"),
                ],
                [
                    uv,
                    "pip",
                    "install",
                    "--no-config",
                    "--python",
                    str(python),
                    "--no-deps",
                    "--reinstall-package",
                    "workflow-studio",
                    str(root / bundle["studio_wheel"]),
                ],
                [uv, "pip", "check", "--no-config", "--python", str(python)],
            ]
            for command in commands:
                subprocess.run(command, check=True, env=env, cwd=root, timeout=600)
            if not check_environment(python, runtime_env, bundle["studio_version"]):
                raise ValueError("Installed Studio core imports/version check failed")
            temporary = owned(root, state / ("studio-environment-" + uuid4().hex + ".tmp"))
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(expected, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, marker)
        finally:
            runtime_lock.rmdir()
    return python


def main(argv=None):
    parser = argparse.ArgumentParser(description="安裝及開啟 RoutineKeel Workflow Studio")
    parser.add_argument("--workspace", type=Path, help="專案工作區；預設解壓縮資料夾")
    parser.add_argument("--prepare-only", action="store_true", help="準備環境後結束，不開啟視窗")
    parser.add_argument(
        "command", choices=("guide", "projects", "status"), nargs="?", default="guide"
    )
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    try:
        bundle = verify_bundle(root)
        runtime_env = Path(sys.prefix).resolve()
        workspace = runtime_env.parent
        if runtime_env.name != ".venv" or workspace not in {root, root.parent}:
            raise ValueError("請透過安裝包啟動器使用工作區的 .venv。")
        python = prepare(root, bundle, workspace)
        if args.prepare_only:
            print(
                json.dumps(
                    {"ok": True, "studio_python": str(python), "runtime_python": str(python)}
                )
            )
            return 0
        command = [
            str(python),
            "-X",
            "utf8",
            "-I",
            "-m",
            "workflow_studio",
            "--workspace",
            str(
                args.workspace
                or (root.parent if (root.parent / "workflows").is_dir() else workspace)
            ),
            "--runtime-python",
            str(python),
            args.command,
        ]
        # Pass model credentials only when the user starts the actual Studio process.
        return subprocess.call(command)
    except Exception as exc:
        print("Studio 啟動失敗；原專案資料保留。請保存此訊息供維護者檢查。", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
