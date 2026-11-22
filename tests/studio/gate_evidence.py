"""Machine-readable acceptance evidence; never contains user inputs or credentials."""

import json
import os
import platform
import sys
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def environment():
    packages = {}
    for name in ("workflow-studio", "flow-base", "pydantic", "filelock"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = "source checkout; distribution metadata unavailable"
    memory = None
    if os.name == "nt":
        import ctypes

        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong)
                for name in (
                    "total",
                    "available",
                    "page_total",
                    "page_available",
                    "virtual_total",
                    "virtual_available",
                    "extended",
                )
            ]

        state = MemoryStatus()
        state.length = ctypes.sizeof(state)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
            memory = state.total
    elif hasattr(os, "sysconf"):
        try:
            memory = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except (ValueError, OSError):
            pass
    return {
        "platform": platform.platform(),
        "python": sys.version,
        "processor": platform.processor(),
        "logical_cpus": os.cpu_count(),
        "physical_memory_bytes": memory,
        "packages": packages,
    }


def report_directory(tmp_path):
    destination = Path(
        os.environ.get("STUDIO_GATE_REPORT_DIR", str(tmp_path / "evidence"))
    ).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    return destination


def write_evidence(tmp_path, name, metrics):
    payload = {
        "schema_version": 1,
        "check": name,
        "recorded_at": datetime.now(UTC).isoformat(),
        "environment": environment(),
        "test_workspace": str(tmp_path),
        "metrics": metrics,
    }
    path = report_directory(tmp_path) / (name + ".json")
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("GATE_EVIDENCE=" + str(path))
    return path
