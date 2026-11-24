import ast
import json
import os
import subprocess
import sys
from pathlib import Path


def test_default_guide_never_imports_tk_or_creates_state(tmp_path):
    root = Path(__file__).resolve().parents[2]
    code = """
import importlib.abc, sys
class NoTk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname == "tkinter" or fullname.startswith("tkinter."):
            raise AssertionError("native UI dependency")
sys.meta_path.insert(0, NoTk())
from workflow_studio.__main__ import main
raise SystemExit(main())
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(root / "workflow_studio/src")},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    guide = json.loads(result.stdout)["result"]
    assert guide["mode"] == "host_conversation"
    assert guide["native_window"] is False and guide["model_calls"] == 0
    assert [layer["layer"] for layer in guide["layers"]] == list(range(9))
    assert not list(tmp_path.iterdir())


def test_studio_sources_have_no_native_windows_imports():
    root = Path(__file__).resolve().parents[2] / "workflow_studio/src"
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                assert all(not n.name.startswith("tkinter") for n in node.names), path
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("tkinter"), path


def test_legacy_ui_command_is_rejected_before_opening_workspace(tmp_path):
    import pytest
    from workflow_studio.__main__ import main

    with pytest.raises(SystemExit) as error:
        main(["--workspace", str(tmp_path), "ui"])
    assert error.value.code == 2
    assert not list(tmp_path.iterdir())
