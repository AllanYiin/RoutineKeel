"""Exercise the Windows launcher with real uv and disposable offline wheels."""

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from flow_base.blueprints.wheel import write_wheel


PWSH = shutil.which("powershell")
UV = shutil.which("uv")
pytestmark = pytest.mark.skipif(os.name != "nt" or not PWSH or not UV or sys.version_info[:2] != (3, 12),
                                reason="Windows PowerShell, uv and CPython 3.12 required")
ROOT = Path(__file__).resolve().parents[1]


def launcher(tmp_path, *, runtime_version="0.3.0"):
    app = tmp_path / "flow_base"
    app.mkdir()
    wheel = app / "flow_base-0.3.0-py3-none-any.whl"
    contracts = app / "routinekeel_contracts-1.0.0-py3-none-any.whl"
    write_wheel(wheel, "flow_base", "0.3.0", {
        "flow_base/__init__.py": f"__version__ = {runtime_version!r}\n".encode(),
        "flow_base/cli.py": b"",
    }, dependencies=())
    write_wheel(contracts, "routinekeel_contracts", "1.0.0", {"routinekeel_contracts/__init__.py": b""},
                dependencies=())
    script = app / "start.ps1"
    source = (ROOT / "tools/start.ps1.in").read_text(encoding="utf-8")
    for key, value in {"@UV_PS_CASES@": "", "@VERSION@": "0.3.0", "@WHEEL@": wheel.name,
                       "@WHEEL_HASH@": hashlib.sha256(wheel.read_bytes()).hexdigest(),
                       "@CONTRACTS_WHEEL@": contracts.name,
                       "@CONTRACTS_SHA256@": hashlib.sha256(contracts.read_bytes()).hexdigest()}.items():
        source = source.replace(key, value)
    script.write_text(source, encoding="utf-8-sig")
    return script


def run_launcher(script):
    # Prefer the running CPython base executable even when this test runs in a venv.
    base_dir = Path(sys._base_executable).parent
    env = {**os.environ, "UV_OFFLINE": "1", "PATH": os.pathsep.join((str(base_dir), os.environ["PATH"]))}
    return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                           "-File", str(script), "--bootstrap-only"], env=env,
                          capture_output=True, encoding="utf-8", errors="replace", timeout=60)


def test_uses_existing_cpython_when_managed_install_location_is_unusable(tmp_path):
    script = launcher(tmp_path)
    state = tmp_path / "share_states"
    state.mkdir()
    (state / "python").write_text("A file cannot be used as a managed Python directory.", encoding="utf-8")
    result = run_launcher(script)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / ".venv/Scripts/python.exe").is_file()
    assert not (state / "base-creating").exists()


def test_failed_runtime_verification_restores_previous_environment(tmp_path):
    venv = tmp_path / ".venv"
    subprocess.run([UV, "venv", "--no-config", "--no-project", "--no-python-downloads",
                    "--python", sys._base_executable, str(venv)], check=True, capture_output=True, timeout=30,
                   env={**os.environ, "UV_CACHE_DIR": str(tmp_path / "setup-cache")})
    keep = venv / "keep.txt"
    keep.write_text("previous environment", encoding="utf-8")
    result = run_launcher(launcher(tmp_path, runtime_version="0.2.0"))
    assert result.returncode != 0
    assert "Installed runtime did not pass" in result.stdout
    assert keep.read_text(encoding="utf-8") == "previous environment"
    state = tmp_path / "share_states"
    assert not (state / "base-creating").exists() and not (state / "bootstrap.lock").exists()


def test_python_extraction_failure_is_logged_before_replacing_venv(tmp_path):
    script = launcher(tmp_path)
    source = script.read_text(encoding="utf-8-sig")
    simulation = """
function Find-FlowBasePython { return $null }
function Invoke-FlowBootstrapUv {
    Write-Host 'failed to extract the Python archive: Access is denied.'
    return 1
}
"""
    script.write_text(source.replace("$Expected =", simulation + "$Expected =", 1), encoding="utf-8-sig")
    venv = tmp_path / ".venv"
    venv.mkdir()
    original = venv / "pyvenv.cfg"
    original.write_text("existing configuration", encoding="utf-8")
    result = run_launcher(script)
    assert result.returncode != 0
    assert original.read_text(encoding="utf-8") == "existing configuration"
    assert not (tmp_path / "share_states/retired_envs").exists()
    log = next((tmp_path / "share_logs/launcher").glob("*.log"))
    assert "failed to extract the Python archive: Access is denied." in log.read_text(encoding="utf-8-sig")
