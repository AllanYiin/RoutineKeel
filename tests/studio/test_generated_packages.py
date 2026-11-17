import zipfile

import pytest
from compiler_helpers import build_ir
from workflow_studio.compiler.package import verify_package
from workflow_studio.contracts import StudioError

pytest.importorskip("PIL", reason="Package fixtures require Studio source dependencies")


def test_generated_package_is_self_contained_deterministic_and_tamper_checked(tmp_path):
    studio, p, revision, policy = build_ir(tmp_path)
    first = studio.compile(p.project_id, revision.revision_id, policy)
    second = studio.compile(p.project_id, revision.revision_id, policy)
    package = first["path"] / "package"
    manifest = verify_package(package)
    assert manifest["sha256"] == verify_package(second["path"] / "package")["sha256"]
    assert first["status"] == "candidate"
    assert (first["path"] / "source/pyproject.toml").is_file()
    with zipfile.ZipFile(package / manifest["package"]) as wheel:
        names = wheel.namelist()
        assert not any("workflow_studio/" in name for name in names)
        assert all(".env" not in name and "sqlite3" not in name for name in names)
        code = wheel.read(manifest["module"] + "/adapter.py").decode()
        assert "import workflow_studio" not in code
        assert str(tmp_path) not in code
    wheel_path = package / manifest["package"]
    wheel_path.write_bytes(wheel_path.read_bytes() + b"tampered")
    with pytest.raises(StudioError):
        verify_package(package)
    studio.close()
