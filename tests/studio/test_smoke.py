import json
import subprocess
import sys
import tomllib
from pathlib import Path

from workflow_studio.service import StudioService
from workflow_studio.storage import MemoryRepository, Repository

from workflow_studio import __version__


def test_service_accepts_storage_and_reports_version():
    repository = MemoryRepository()
    assert isinstance(repository, Repository)
    with StudioService(repository) as service:
        assert service.status() == {"version": __version__, "schema_version": 0}


def test_package_metadata_matches():
    root = Path(__file__).resolve().parents[2] / "workflow_studio"
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["version"] == __version__


def test_import_does_not_import_runtime_or_create_state(tmp_path):
    source = Path(__file__).resolve().parents[2] / "workflow_studio/src"
    script = (
        "import sys,json; sys.path.insert(0,sys.argv[1]); "
        "from workflow_studio.service import StudioService; "
        "print(json.dumps([m for m in sys.modules if m.startswith('flow_base')]))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(source)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == []
    assert list(tmp_path.iterdir()) == []
