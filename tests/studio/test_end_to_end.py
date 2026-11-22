import json
import os
import subprocess
from pathlib import Path

from compiler_helpers import runtime_python
from gate_evidence import report_directory, write_evidence
from test_governance import verified_approval
from workflow_studio.governance.release import Releases


def test_hundred_different_inputs_keep_published_outputs_and_ids_isolated(tmp_path):
    studio, project, build, _, _, policy, approval = verified_approval(tmp_path)
    root = tmp_path / "installed"
    release = Releases(studio).publish(project.project_id, approval["approval_id"], root, policy)
    manifest = (root / "copy-example/flow.json").read_bytes()
    directory = report_directory(tmp_path)
    result_file = directory / "isolation-runs.json"
    runner = Path(__file__).with_name("runtime_isolation_runner.py")
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper()
        in {
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "PATH",
            "PATHEXT",
            "TEMP",
            "TMP",
            "HOME",
            "USERPROFILE",
            "LANG",
            "LC_ALL",
        }
    }
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        [str(runtime_python()), "-I", str(runner), str(root), str(result_file)],
        env=env,
        capture_output=True,
        timeout=600,
        check=False,
    )
    (directory / "isolation-console.log").write_bytes(result.stdout + result.stderr)
    assert result.returncode == 0, (result.stdout + result.stderr)[-12000:].decode(
        "utf-8", errors="replace"
    )
    metrics = json.loads(result_file.read_text(encoding="utf-8"))
    assert metrics["runs"] == 100 and metrics["mixed_outputs"] == 0 and metrics["passed"]
    assert metrics["occupied_output_reuse_refused"] == 1
    assert (root / "copy-example/flow.json").read_bytes() == manifest
    assert Releases(studio).current(root, "copy-example")["release_id"] == release["release_id"]
    metrics.update(
        package_hash=build["package_hash"],
        release_id=release["release_id"],
        runtime_python=str(runtime_python()),
    )
    write_evidence(tmp_path, "isolation-100", metrics)
    studio.close()
