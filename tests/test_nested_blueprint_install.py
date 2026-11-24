import subprocess
import sys
from pathlib import Path

import pytest
from flow_base.workspace import Workspace
from test_blueprint_install import apply, candidate

pytestmark = pytest.mark.usefixtures("desktop_test_catalog")


def test_nested_first_install_uses_shared_state_and_preserves_workflow_identity(tmp_path):
    (tmp_path / 'workflows').mkdir()
    service, draft, build = candidate(tmp_path)
    receipt = apply(service, draft, build)
    folder = tmp_path / 'workflows/FLOW_example_flow'
    assert Path(receipt['folder']) == folder
    assert service.workspace.task_at(folder).manifest.id == 'example_flow'
    assert service.workspace.runtime.workspace_root == tmp_path
    assert service.workspace.runtime.home == tmp_path / 'flow_base/share_states'
    assert not (tmp_path / 'share_states').exists()
    assert Workspace(tmp_path).task_at(folder).manifest.id == 'example_flow'


def test_repository_directory_is_not_an_installed_studio_validator():
    root = Path(__file__).resolve().parents[1]
    code = (
        'import sys; sys.path.insert(0, sys.argv[1]);'
        'from flow_base.blueprints.ir import check_with_studio;'
        'sys.modules["routinekeel_contracts.validator"] = None;'
        'assert check_with_studio(None,None,None)["status"] == "NOT_RUN"'
    )
    result = subprocess.run(
        [sys.executable, '-c', code, str(root / 'flow_base/src')],
        cwd=root, capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == 0, result.stderr
