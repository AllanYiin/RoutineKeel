import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ir_cases import valid_ir
from workflow_studio.registry.policy import ToolPolicy
from workflow_studio.service import StudioService

ROOT = Path(__file__).resolve().parents[2]


def build_ir(tmp_path, payload=None, policy=None):
    payload = payload or valid_ir()
    studio = StudioService.open_workspace(tmp_path / "studio")
    p = studio.projects.create("編譯", payload["goal"])
    source = studio.sources.import_text(p.project_id, 1, "測試來源規格")
    studio.tools.seed()
    for evidence in payload["evidence"]:
        evidence["source_id"] = source.source_id
    policy = policy or ToolPolicy("windows" if os.name == "nt" else "linux")
    revision = studio.save_ir(p.project_id, 2, payload, policy)
    return studio, p, revision, policy


def runtime_python():
    # Independent Runtime environment is intentional: generated wheels must not need Studio.
    candidate = (
        ROOT / "flow_base/.venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    return str(candidate) if candidate.is_file() else sys.executable


def execute_package(package, workspace, content, output_name="output"):
    workspace.mkdir(parents=True, exist_ok=True)
    task = workspace / "generated"
    if not task.exists():
        shutil.copytree(package, task)
    source = workspace / "input.txt"
    source.write_bytes(content)
    script = """
import json, sys
from pathlib import Path
from flow_base.core.service import WorkflowService
from flow_base.core.types import PlanRequest, StartRequest
root = Path(sys.argv[1])
output = root / sys.argv[2]
service = WorkflowService(root)
planned = service.plan_workflow(PlanRequest(task='generated',source=root/'input.txt',output=output))
assert planned.plan['ready_to_prepare']
assert service.prepare_workflow('generated',offline=True)['ok']
assert service.verify_workflow('generated')['ok']
result = service.run_workflow_sync(StartRequest(task='generated',source=root/'input.txt',output=output,offline=True))
assert result['token_usage']['registered_requests'] == 0
result['_lifecycle'] = [event['name'] for event in service.list_run_events(result['run_id'])['events']]
assert {'run.started','delivery.published','run.finished'} <= set(result['_lifecycle'])
print('REPORT_JSON=' + json.dumps(result))
"""
    env = dict(os.environ, PYTHONPATH=str(ROOT / "flow_base/src"), PYTHONIOENCODING="utf-8")
    run = subprocess.run(
        [runtime_python(), "-c", script, str(workspace), output_name],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads(
        next(
            line.removeprefix("REPORT_JSON=")
            for line in run.stdout.splitlines()
            if line.startswith("REPORT_JSON=")
        )
    )
    return report
