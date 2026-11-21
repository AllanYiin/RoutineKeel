import json
import zipfile
from pathlib import Path

import pytest
from compiler_helpers import execute_package
from test_verifier import verification_case
from workflow_studio.compiler.package import digest, write_wheel
from workflow_studio.compiler.template_registry import verify_template


def test_pre_generation_template_remains_verifiable_and_executable(tmp_path):
    studio, p, build, cases, verifier, policy = verification_case(tmp_path)
    package = build["path"] / "package"
    manifest = json.loads((package / "flow.json").read_text(encoding="utf-8"))
    wheel = package / manifest["package"]
    with zipfile.ZipFile(wheel) as archive:
        compiled = archive.read(manifest["module"] + "/compiled.json")
    import workflow_studio.compiler.template_registry as registry

    old_code = (Path(registry.__file__).parent / "templates/v1/runtime_adapter.py.txt").read_bytes()
    assert verify_template("runtime_adapter.py", old_code) == "v1"
    assert verify_template("runtime_adapter.py", old_code.replace(b"\n", b"\r\n")) == "v1"
    candidate = build["path"] / "legacy.whl"
    write_wheel(
        candidate,
        manifest["module"],
        manifest["version"],
        {
            manifest["module"] + "/__init__.py": b"",
            manifest["module"] + "/adapter.py": old_code,
            manifest["module"] + "/compiled.json": compiled,
        },
    )
    wheel.write_bytes(candidate.read_bytes())
    manifest["sha256"] = digest(wheel.read_bytes())
    (package / "flow.json").write_text(json.dumps(manifest), encoding="utf-8")
    bundle = next(build["path"].glob("*.zip"))
    with zipfile.ZipFile(bundle, "w") as archive:
        for item in package.iterdir():
            archive.write(item, manifest["module"] + "/" + item.name)
    studio.repository.connection.execute(
        "UPDATE studio_builds SET package_hash=? WHERE build_id=?",
        (digest(bundle.read_bytes()), build["build_id"]),
    )
    report = verifier.verify(p.project_id, build["build_id"], cases, policy)
    assert report["status"] == "passed", report
    runtime = execute_package(package, tmp_path / "runtime-legacy", b"old package remains usable")
    assert runtime["status"] == "complete"
    studio.close()


def test_unknown_template_does_not_gain_trust_from_a_filename():
    from workflow_studio.contracts import StudioError

    with pytest.raises(StudioError):
        verify_template("runtime_adapter.py", b"print('not a trusted version')")
