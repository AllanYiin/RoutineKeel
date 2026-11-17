import json
import shutil
import zipfile
from pathlib import Path
from uuid import uuid4

from ..contracts import StudioError
from ..ir.validator import validate_ir
from ..ir.versioning import ir_fingerprint
from ..project.models import canonical_json
from ..project.service import now
from .compatibility import check_supported
from .lowering import lower
from .package import digest, verify_package, write_wheel


class Compiler:
    def __init__(self, studio):
        self.studio = studio

    def compile(self, project_id, revision_id, policy):
        revision = self.studio.projects.get_revision(project_id, revision_id)
        if (
            revision.status not in {"ir_ready", "verified", "approved", "published"}
            or revision.discarded
        ):
            raise StudioError("IR_SCHEMA_INVALID", "請先保存通過驗證的 IR 版本。")
        tools = self.studio.tools.allowed(policy)
        ir = validate_ir(revision.ir, {k: t.signature() for k, t in tools.items()})
        check_supported(ir, tools)
        has_llm = any(n.kind == "llm" for n in ir.nodes)
        if (
            sum(n.llm_policy.budget_micro_usd for n in ir.nodes if n.llm_policy)
            > policy.max_cost_micro_usd
        ):
            raise StudioError("BUDGET_EXCEEDED", "執行期模型預算超過本次核准上限。")
        compiled = lower(ir, tools)
        ir_hash = ir_fingerprint(ir)
        module = "studio_" + ir.workflow_id.replace("-", "_") + "_" + ir_hash[:12]
        version = f"0.1.{ir.revision}"
        build_id = "build_" + uuid4().hex
        base = self.studio.repository.root
        # Generated locations use database-owned IDs, never input file names or model paths.
        destination = base / "projects" / project_id / "builds" / build_id
        if not destination.resolve().is_relative_to(base):
            raise StudioError("INPUT_INVALID", "建置路徑超出 Studio 儲存區。")
        for parent in destination.parents:
            if parent == base:
                break
            if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
                raise StudioError("INPUT_INVALID", "建置目錄不能經過連結。")
        destination.mkdir(parents=True, exist_ok=False)
        package = destination / "package"
        package.mkdir()
        template = Path(__file__).with_name("runtime_adapter.py").read_bytes()
        compiled_bytes = (
            json.dumps(compiled, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode()
        files = {
            f"{module}/__init__.py": b"",
            f"{module}/adapter.py": template,
            f"{module}/compiled.json": compiled_bytes,
        }
        if has_llm:
            files[f"{module}/llm_runtime.py"] = (
                Path(__file__).with_name("runtime_llm.py").read_bytes()
            )
        wheel = package / f"{module}-{version}-py3-none-any.whl"
        write_wheel(wheel, module, version, files)
        manifest = {
            "schema_version": 1,
            "runtime_api": "0.2",
            "output_layout": "categorized-v1",
            "id": ir.workflow_id,
            "module": module,
            "version": version,
            "title": ir.goal.description,
            "description": "由 Studio 產生的版本化工作流；候選包需獨立驗證後才可發布。",
            "package": wheel.name,
            "sha256": digest(wheel.read_bytes()),
            "keywords": [ir.workflow_id],
            "llm_policy": "optional" if has_llm else "forbidden",
            "outputs": compiled["runtime_workflow"]["outputs"],
            "defaults": {},
        }
        (package / "flow.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (package / "README.md").write_text(
            f"# {ir.goal.description}\n\n候選工作流 {version}。尚未獨立驗證或正式發布。\n"
            "放入隔離的 RoutineKeel 測試工作區，透過底座 plan／prepare／verify／run 執行。\n"
            + (
                "本版本含有模型節點，需明確授權、憑證與費率。\n"
                if has_llm
                else "本版本不呼叫模型。\n"
            )
            + "單檔上限 10 MiB；不允許就地覆寫參數。輸出見 report.json 的 named_outputs。\n",
            encoding="utf-8",
        )
        (package / "workflow.ir.json").write_text(canonical_json(ir.model_dump()), encoding="utf-8")
        (package / "PROVENANCE.md").write_text(
            f"# 編譯證據\n\nIR SHA-256: {ir_hash}\n\nRuntime API: 0.2；來源引用保留在 workflow.ir.json，未包含原始來源內容。\n",
            encoding="utf-8",
        )
        source = destination / "source"
        (source / "src" / module).mkdir(parents=True)
        for name, data in files.items():
            (source / "src" / name).write_bytes(data)
        (source / "pyproject.toml").write_text(
            f'[build-system]\nrequires=["hatchling==1.27.0"]\nbuild-backend="hatchling.build"\n'
            f'[project]\nname="{module}"\nversion="{version}"\nrequires-python=">=3.11,<3.14"\n'
            f'[tool.hatch.build.targets.wheel]\npackages=["src/{module}"]\n',
            encoding="utf-8",
        )
        (source / "flow.template.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        shutil.copyfile(package / "README.md", source / "README.md")
        verify_package(package)
        archive_path = destination / f"{module}-{version}.zip"
        with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(package.iterdir()):
                archive.write(file, module + "/" + file.name)
        mapping = {
            "ir_hash": ir_hash,
            "runtime_api": "0.2",
            "input_step": compiled["input_step"],
            "steps": [s["id"] for s in compiled["runtime_workflow"]["steps"]],
            "outputs": compiled["runtime_workflow"]["outputs"],
            "limitations": [
                "single_file_input",
                "trusted_deterministic_handlers",
                "10MiB_per_file",
            ],
        }
        (destination / "mapping.json").write_text(canonical_json(mapping), encoding="utf-8")
        with self.studio.repository.transaction() as db:
            db.execute(
                "INSERT INTO studio_builds VALUES (?,?,?,?,?,?,?,?)",
                (
                    build_id,
                    project_id,
                    revision_id,
                    destination.relative_to(base).as_posix(),
                    digest(archive_path.read_bytes()),
                    "candidate",
                    canonical_json(mapping),
                    now(),
                ),
            )
        return self.get(project_id, build_id)

    def get(self, project_id, build_id):
        row = self.studio.repository.connection.execute(
            "SELECT * FROM studio_builds WHERE project_id=? AND build_id=?", (project_id, build_id)
        ).fetchone()
        if row is None:
            raise StudioError("BUILD_NOT_FOUND", "找不到建置版本。")
        result = dict(row)
        result["path"] = self.studio.repository.root / result.pop("relative_path")
        result["mapping"] = json.loads(result.pop("mapping_json"))
        return result
