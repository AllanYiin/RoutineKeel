"""Run cross-host acceptance with the staged optional adapter and both isolated interpreters."""

import os
import subprocess
from pathlib import Path
from xml.etree import ElementTree


def python_in(root, project):
    binary = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return root / project / ".venv" / binary


def main():
    root = Path(__file__).resolve().parents[2]
    output = Path(os.environ["STUDIO_GATE_REPORT_DIR"]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    wheels = output / "optional-wheels"
    wheels.mkdir(exist_ok=True)
    base = python_in(root, "flow_base")
    studio = python_in(root, "workflow_studio")
    subprocess.run(
        [
            "uv",
            "build",
            "--wheel",
            "--out-dir",
            str(wheels),
            str(root / "capability_packs/data_adapter"),
        ],
        check=True,
        cwd=root,
        timeout=180,
    )
    (adapter,) = wheels.glob("routinekeel_data_adapter-*.whl")
    subprocess.run(
        ["uv", "pip", "install", "--no-deps", "--python", str(base), str(adapter)],
        check=True,
        cwd=root,
        timeout=180,
    )
    env = {
        **os.environ,
        "ROUTINEKEEL_STUDIO_PYTHON": str(studio),
        "ROUTINEKEEL_E2E_ENFORCE": "1",
        "ROUTINEKEEL_E2E_EVIDENCE": str(output / "pattern-portability.json"),
    }
    subprocess.run(
        [
            str(base),
            "-m",
            "pytest",
            "tests/e2e/test_pattern_portability.py",
            "-q",
            "-rs",
            f"--junitxml={output / 'pattern-portability-junit.xml'}",
        ],
        check=True,
        cwd=root,
        env=env,
        timeout=600,
    )
    document = ElementTree.parse(output / "pattern-portability-junit.xml")
    tests = list(document.iter("testcase"))
    if not tests or any(case.find("skipped") is not None for case in tests):
        raise RuntimeError("Cross-host acceptance must run in the prepared environment")


if __name__ == "__main__":
    main()
