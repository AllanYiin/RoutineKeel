import json
import os
import subprocess
import sys
import venv
from pathlib import Path

import pytest
from workflow_studio.verifier.report import Verifier


@pytest.fixture(scope="module")
def runtime_environment(tmp_path_factory):
    root = tmp_path_factory.mktemp("independent-runtime")
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(root)
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return root, python


@pytest.mark.parametrize("selection", ["explicit", "relative", "current"])
def test_verifier_launches_selected_virtual_environment(
    runtime_environment, monkeypatch, selection
):
    root, python = runtime_environment
    if selection == "relative":
        monkeypatch.chdir(root.parent)
        selected = python.relative_to(root.parent)
    elif selection == "current":
        monkeypatch.setattr(sys, "executable", str(python))
        selected = None
    else:
        selected = python
    verifier = Verifier(None, selected)
    run = subprocess.run(
        [
            str(verifier.runtime_python),
            "-I",
            "-c",
            "import json,sys; print(json.dumps(sys.prefix))",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert Path(json.loads(run.stdout)).samefile(root)
