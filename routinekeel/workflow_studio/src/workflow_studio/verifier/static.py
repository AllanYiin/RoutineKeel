"""Independent integrity and executable-template checks before any dynamic replay."""

import io
import json
import zipfile
from pathlib import Path

from ..compiler.compatibility import check_supported
from ..compiler.lowering import lower
from ..compiler.package import digest, verify_package
from ..compiler.template_registry import verify_template
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
    ir = validate_ir(
        revision.ir,
        {k: t.signature() for k, t in tools.items()},
        verified_records=studio.confirmed_evidence(build["project_id"], revision.ir),
    )
    check_supported(ir, tools)
    if (
        sum(n.llm_policy.budget_micro_usd for n in ir.nodes if n.llm_policy)
        > policy.max_cost_micro_usd
    ):
        raise StudioError("BUDGET_EXCEEDED", "目前政策未允許此模型預算。")
    if ir_fingerprint(ir) != build["mapping"]["ir_hash"]:
        raise StudioError("TEST_FAILED", "建置 IR 指紋不符。")
    expected_plan = lower(ir, tools)
    has_llm = any(n.kind == "llm" for n in ir.nodes)
    generated = [
        n
        for n in ir.nodes
        if tools[f"{n.tool}@{n.tool_version}"].implementation == "generated.program"
    ]
    from ..compiler.handlers import verify_examples

    expected_files = {
        "flow.json",
        "README.md",
        "workflow.ir.json",
        "PROVENANCE.md",
        manifest["package"],
    }
    if generated:
        expected_files |= {f"handler_{node.id}.py" for node in generated} | {
            "generated-handler-tests.json"
        }
    if set(verified_files) != expected_files:
        raise StudioError("TEST_FAILED", "候選包含有未宣告或缺少的交付檔案。")
    expected_tests = {}
    for node in generated:
        if verified_files.get(f"handler_{node.id}.py") != node.parameters["program"].encode():
            raise StudioError("TEST_FAILED", "生成的 Handler 原始碼與 IR 不一致。")
        expected_tests[node.id] = {
            "examples": node.parameters["tests"],
            "result": verify_examples(node.parameters, node.outputs["result"].format),
        }
    if generated and json.loads(verified_files.get("generated-handler-tests.json", b"{}")) != {
        "evidence_kind": "synthetic_examples_only",
        "handlers": expected_tests,
    }:
        raise StudioError("TEST_FAILED", "生成的 Handler 測試紀錄無法重現。")
    with zipfile.ZipFile(io.BytesIO(verified_files[manifest["package"]])) as wheel:
        allowed = {prefix + name for name in ("__init__.py", "adapter.py", "compiled.json")}
        if has_llm:
            allowed.add(prefix + "llm_runtime.py")
            verify_template("runtime_llm.py", wheel.read(prefix + "llm_runtime.py"))
        if generated:
            allowed.add(prefix + "program_runtime.py")
            if (
                wheel.read(prefix + "program_runtime.py")
                != (Path(__file__).parents[1] / "compiler/runtime_program.py").read_bytes()
            ):
                raise StudioError("TEST_FAILED", "生成程式的執行器不是已驗證版本。")
        allowed |= {
            f"{manifest['module']}-{manifest['version']}.dist-info/{name}"
            for name in ("METADATA", "WHEEL", "RECORD")
        }
        if set(wheel.namelist()) != allowed:
            raise StudioError("TEST_FAILED", "候選 wheel 含未允許的可執行內容。")
        adapter_version = verify_template("runtime_adapter.py", wheel.read(prefix + "adapter.py"))
        if wheel.read(prefix + "__init__.py") != b"" or (
            generated and adapter_version != "current"
        ):
            raise StudioError("TEST_FAILED", "Handler 不是已驗證的受控模板。")
        if json.loads(wheel.read(prefix + "compiled.json")) != expected_plan:
            raise StudioError("TEST_FAILED", "生成內容與已保存的 IR 不一致。")
    if (
        manifest.get("id") != ir.workflow_id
        or manifest.get("version") != f"0.1.{ir.revision}"
        or not manifest.get("module", "").startswith(
            "studio_" + ir.workflow_id.replace("-", "_") + "_"
        )
        or manifest.get("outputs") != expected_plan["runtime_workflow"]["outputs"]
        or manifest.get("defaults") != {}
        or manifest.get("llm_policy") != ("optional" if has_llm else "forbidden")
        or manifest.get("runtime_api") != "0.2"
        or manifest.get("schema_version") != 1
    ):
        raise StudioError("TEST_FAILED", "Manifest 與執行契約不符。")
    return manifest, verified_files
