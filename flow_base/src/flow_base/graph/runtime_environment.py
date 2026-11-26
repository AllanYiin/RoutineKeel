"""Facts about the interpreter actually resolving workspace bindings."""

import platform
import sys
from pathlib import Path

from routinekeel_contracts.runtime_environment import python_in, workspace_environment

from .. import __version__
from ..runtime import digest


def binding_environment(workspace):
    root = Path(workspace).resolve()
    expected = workspace_environment(root)
    prefix = Path(sys.prefix).resolve()
    result = {
        "workspace": str(root),
        "python_executable": str(Path(sys.executable).absolute()),
        "python_prefix": str(prefix),
        "python_version": platform.python_version(),
        "flow_base_version": __version__,
        "platform": sys.platform,
        "architecture": platform.machine(),
        "expected_python": str(python_in(expected)) if expected else None,
        "status": "ready" if expected is None or expected.resolve() == prefix else "mismatch",
    }
    return {**result, "runtime_id": digest(result)}


def runtime_mismatch(environment):
    return {
        "node": "runtime", "tool": "flow_base", "code": "RUNTIME_MISMATCH",
        "reason": "目前 Python 與工作區的 Runtime 不符。請使用這個工作區的底座啟動器後重驗。",
        "expected_python": environment["expected_python"],
        "actual_python": environment["python_executable"],
    }
