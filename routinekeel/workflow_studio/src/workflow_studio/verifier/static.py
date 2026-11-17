"""Independent integrity and executable-template checks before any dynamic replay."""

import io
import json
import zipfile
from pathlib import Path

from ..compiler.compatibility import check_supported
from ..compiler.lowering import lower
from ..compiler.package import digest, verify_package
from ..contracts import StudioError
from ..ir.validator import validate_ir
from ..ir.versioning import ir_fingerprint


def verify_static(studio, build, policy):
    folder = build["path"]
    archives = list(folder.glob("*.zip"))
    if (
        len(archives) != 1
        or archives[0].is_symlink()
        or digest(archives[0].read_bytes()) != build["package_hash"]
    ):
        raise StudioError("TEST_FAILED", "建置 ZIP 與登錄指紋不符。")
    package = folder / "package"
    manifest = verify_package(package)
    prefix = manifest["module"] + "/"
    verified_files = {}
    with zipfile.ZipFile(archives[0]) as archive:
        expected = {prefix + p.name for p in package.iterdir() if p.is_file()}
        if set(archive.namelist()) != expected:
            raise StudioError("TEST_FAILED", "候選 ZIP 與流程包檔案清單不一致。")
        for name in expected:
            path = package / name.removeprefix(prefix)
            if path.is_symlink() or archive.read(name) != path.read_bytes():
                raise StudioError("TEST_FAILED", "候選檔案與 ZIP 內容不同。")
            verified_files[name.removeprefix(prefix)] = archive.read(name)
    revision = studio.projects.get_revision(build["project_id"], build["revision_id"])
    tools = studio.tools.allowed(policy)
    ir = validate_ir(revision.ir, {k: t.signature() for k, t in tools.items()})
    check_supported(ir, tools)
    if ir_fingerprint(ir) != build["mapping"]["ir_hash"]:
        raise StudioError("TEST_FAILED", "建置 IR 指紋不符。")
    expected_plan = lower(ir, tools)
    trusted_template = Path(__file__).parents[1] / "compiler/runtime_adapter.py"
    with zipfile.ZipFile(io.BytesIO(verified_files[manifest["package"]])) as wheel:
        allowed = {prefix + name for name in ("__init__.py", "adapter.py", "compiled.json")}
        allowed |= {
            f"{manifest['module']}-{manifest['version']}.dist-info/{name}"
            for name in ("METADATA", "WHEEL", "RECORD")
        }
        if set(wheel.namelist()) != allowed:
            raise StudioError("TEST_FAILED", "候選 wheel 含未允許的可執行內容。")
        if (
            wheel.read(prefix + "adapter.py") != trusted_template.read_bytes()
            or wheel.read(prefix + "__init__.py") != b""
        ):
            raise StudioError("TEST_FAILED", "Handler 不是已驗證的受控模板。")
        if json.loads(wheel.read(prefix + "compiled.json")) != expected_plan:
            raise StudioError("TEST_FAILED", "生成內容與已保存的 IR 不一致。")
    if (
        manifest.get("outputs") != expected_plan["runtime_workflow"]["outputs"]
        or manifest.get("defaults") != {}
        or manifest.get("llm_policy") != "forbidden"
        or manifest.get("runtime_api") != "0.2"
        or manifest.get("schema_version") != 1
    ):
        raise StudioError("TEST_FAILED", "Manifest 與執行契約不符。")
    return manifest, verified_files
