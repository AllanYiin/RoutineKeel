"""Exercise the delivered launchers and installed wheels without opening a desktop."""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

PROBE = r'''
import importlib.util, json, subprocess, sys
from pathlib import Path
import workflow_studio
from workflow_studio.ui.model import StudioUIModel
root, runtime = Path(sys.argv[1]), Path(sys.argv[2])
assert importlib.util.find_spec('flow_base') is None
assert Path(workflow_studio.__file__).is_relative_to(Path(sys.prefix))
model = StudioUIModel(root/'design-test', runtime_python=runtime)
project = model.create_project('Installed example', '完整複製輸入檔', '成果內容相同')
source = model.import_path(root/'examples/first-copy')
candidates, _ = model.folder_candidates(source.source_id)
assert candidates
revision = model.accept_candidate(0)
report = model.verify()
assert report['status'] == 'passed', report
release = model.approve_publish(root/'published-test', 'distribution fixture', 'engineering test only')
reopened = StudioUIModel(root/'design-test', runtime_python=runtime)
reopened.select_project(project.project_id)
assert reopened.revision().ir == revision.ir and reopened.release_history()
script = """
import hashlib, importlib.util, json, sys
from pathlib import Path
import flow_base
from flow_base.core.service import WorkflowService
from flow_base.core.types import PlanRequest, StartRequest
assert importlib.util.find_spec('workflow_studio') is None
assert Path(flow_base.__file__).is_relative_to(Path(sys.prefix))
root, task = Path(sys.argv[1]), sys.argv[2]
source, output = root/'second.txt', root/'second-output'
source.write_bytes(b'distribution second input\\n')
service = WorkflowService(root)
plan = service.plan_workflow(PlanRequest(task=task, source=source, output=output))
assert plan.plan['ready_to_prepare']
assert service.prepare_workflow(task, offline=True)['ok']
assert service.verify_workflow(task)['ok']
report = service.run_workflow_sync(StartRequest(task=task, source=source, output=output, offline=True))
assert report['status'] == 'complete' and report['token_usage']['registered_requests'] == 0
primary = output/report['named_outputs']['result']['primary']
artifact = json.loads(primary.read_text(encoding='utf-8'))['result']
actual = (primary.parent/artifact['path']).read_bytes()
assert actual == source.read_bytes() and hashlib.sha256(actual).hexdigest() == artifact['sha256']
print('RUNTIME_CHECK='+json.dumps({'run_id': report['run_id'], 'runtime_file': flow_base.__file__}))
"""
run = subprocess.run([str(runtime), '-X', 'utf8', '-I', '-c', script, str(root/'published-test'), release['workflow_id']], capture_output=True, text=True, encoding='utf-8', timeout=120)
assert run.returncode == 0, run.stdout + run.stderr
runtime_result = json.loads(next(line.removeprefix('RUNTIME_CHECK=') for line in run.stdout.splitlines() if line.startswith('RUNTIME_CHECK=')))
print('STUDIO_CHECK='+json.dumps({'passed': True, 'studio_file': workflow_studio.__file__, 'report_id': report['report_id'], 'release_id': release['release_id'], 'runtime': runtime_result, 'registered_model_calls': 0}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--require-clean", action="store_true", help="Require committed build inputs"
    )
    parser.add_argument(
        "--work-dir", type=Path, help="Optional short parent directory for fresh installs"
    )
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.work_dir:
        args.work_dir.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="studio-dist-", dir=args.work_dir)).resolve()
    extraction = temporary / "Studio space 中文"
    extraction.mkdir()
    with zipfile.ZipFile(args.archive) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names)) < 10000
        assert sum(item.file_size for item in archive.infolist()) < 100 * 1024 * 1024
        for item in archive.infolist():
            name = PurePosixPath(item.filename)
            assert not name.is_absolute() and ".." not in name.parts
            assert name.parts[0] == "workflow_studio" and "\\" not in item.filename
            assert ":" not in item.filename
            assert (item.external_attr >> 16) & 0o170000 != 0o120000
            assert not {".venv", ".studio-venv", "share_states", "share_logs", ".git"} & set(
                name.parts
            )
            if item.filename.endswith((".sh", ".command")):
                assert (item.external_attr >> 16) & 0o111
        archive.extractall(extraction)
    root = extraction / "workflow_studio"
    bundle = json.loads((root / "bundle.json").read_text(encoding="utf-8"))
    if args.require_clean:
        assert not bundle["source_dirty"], "Distribution was built from uncommitted inputs"
    launcher = (
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "start_studio.ps1"),
        ]
        if os.name == "nt"
        else ["sh", str(root / "open_studio.sh")]
    )
    allowed_environment = {
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
        "PROCESSOR_ARCHITECTURE",
        "PROCESSOR_ARCHITEW6432",
    }
    environment = {k: v for k, v in os.environ.items() if k.upper() in allowed_environment}
    environment.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")

    def invoke(arguments, name):
        result = subprocess.run(
            launcher + arguments, capture_output=True, timeout=900, cwd=temporary, env=environment
        )
        (output / (name + ".log")).write_bytes(result.stdout + result.stderr)
        return result

    prepared = invoke(["--prepare-only"], "prepare")
    assert prepared.returncode == 0, (prepared.stdout + prepared.stderr).decode(
        "utf-8", errors="replace"
    )[-8000:]
    marker = root / "share_states/studio-environment.json"
    before = (marker.read_bytes(), marker.stat().st_mtime_ns)
    status = invoke(["status"], "second-launch")
    assert status.returncode == 0, (status.stdout + status.stderr).decode(
        "utf-8", errors="replace"
    )[-8000:]
    assert before == (marker.read_bytes(), marker.stat().st_mtime_ns)
    assert not list((root / "share_states/retired_studio_envs").iterdir())
    if os.name == "nt":
        batch = subprocess.run(
            ["cmd.exe", "/d", "/c", str(root / "open_studio.bat"), "status"],
            input=b"\n",
            capture_output=True,
            timeout=90,
            cwd=temporary,
            env=environment,
        )
        (output / "batch-launcher.log").write_bytes(batch.stdout + batch.stderr)
        assert batch.returncode == 0, (batch.stdout + batch.stderr).decode(
            "utf-8", errors="replace"
        )[-8000:]
        assert before == (marker.read_bytes(), marker.stat().st_mtime_ns)
    executable = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    studio, runtime = root / ".studio-venv" / executable, root / ".venv" / executable
    probe = output / "probe.py"
    probe.write_text(PROBE, encoding="utf-8")
    checked = subprocess.run(
        [str(studio), "-X", "utf8", "-I", str(probe), str(root), str(runtime)],
        capture_output=True,
        timeout=240,
        cwd=temporary,
        env=environment,
    )
    (output / "installed-example.log").write_bytes(checked.stdout + checked.stderr)
    assert checked.returncode == 0, (checked.stdout + checked.stderr).decode(
        "utf-8", errors="replace"
    )[-8000:]
    result = json.loads(
        next(
            line.removeprefix("STUDIO_CHECK=")
            for line in checked.stdout.decode("utf-8").splitlines()
            if line.startswith("STUDIO_CHECK=")
        )
    )
    wheel = root / bundle["studio_wheel"]
    original = wheel.read_bytes()
    try:
        wheel.write_bytes(original + b"tampered fixture")
        refused = invoke(["--prepare-only"], "tampered-package")
        assert refused.returncode != 0
        assert before == (marker.read_bytes(), marker.stat().st_mtime_ns)
    finally:
        wheel.write_bytes(original)
    result.update(
        archive_sha256=hashlib.sha256(args.archive.read_bytes()).hexdigest(),
        source_commit=bundle["source_commit"],
        source_dirty=bundle["source_dirty"],
        reused_environment=True,
        tamper_refused=True,
        native_ui=False,
        scope="Real packaged launcher/install/reopen/Runtime; not native GUI or human/model quality",
    )
    (output / "distribution.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
