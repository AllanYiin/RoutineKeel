"""Locate the Python environment owned by a RoutineKeel workspace."""

import os
from pathlib import Path


def python_in(environment):
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def workspace_environment(workspace):
    root = Path(workspace).absolute()
    base = root / "flow_base"
    shared = root / ".venv"
    if shared.exists() or any((base / name).is_file() for name in ("start.ps1", "start.sh")):
        return shared
    development = base / ".venv"
    if development.exists():
        return development
    return None
