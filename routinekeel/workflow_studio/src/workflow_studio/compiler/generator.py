import json
import shutil
import zipfile
from pathlib import Path
from uuid import uuid4

from ..contracts import StudioError
from ..ir.validator import validate_ir
from ..ir.versioning import ir_fingerprint
from ..llm.streaming import EventStore
from ..project.models import canonical_json
from ..project.service import now
from .compatibility import check_supported
from .lowering import lower
from .package import digest, verify_package, write_wheel
from .template_registry import package_namespace


class Compiler:
    def __init__(self, studio):
        self.studio = studio

    def compile(self, project_id, revision_id, policy):
        # Resolve ownership before writing events for a caller-provided identity.
        self.studio.projects.get_revision(project_id, revision_id)
        events = EventStore(self.studio.repository)
        build_id = "build_" + uuid4().hex
        stage = "started"
        events.append(project_id, None, "generation.started", {"build_id": build_id}, revision_id)

        def progress(completed_stage, content):
            nonlocal stage
            events.append(
                project_id,
                None,
                "generation.delta",
                {"build_id": build_id, "stage": completed_stage, "content": content},
                revision_id,
            )
            stage = completed_stage

        try:
            return self._compile(project_id, revision_id, policy, build_id, progress)
        except (Exception, KeyboardInterrupt) as exc:
            events.append(
                project_id,
                None,
                "generation.failed",
                {
                    "build_id": build_id,
                    "code": exc.code if isinstance(exc, StudioError) else "GENERATION_FAILED",
                    "last_completed_stage": stage,
                },
                revision_id,
            )
            raise

    def _compile(self, project_id, revision_id, policy, build_id, progress):
        if self.studio.projects.get(project_id).status != "active":
            raise StudioError("PROJECT_INACTIVE", "請先恢復專案後再建立候選包。")
        revision = self.studio.projects.get_revision(project_id, revision_id)
        if (
            revision.status not in {"ir_ready", "verified", "approved", "published"}
            or revision.discarded
        ):
            raise StudioError("IR_SCHEMA_INVALID", "請先保存通過驗證的 IR 版本。")
        tools = self.studio.tools.allowed(policy)
        self.studio.handlers.validate_snapshot(revision.ir, tools)
        ir = validate_ir(
            revision.ir,
            {k: t.signature() for k, t in tools.items()},
            verified_records=self.studio.confirmed_evidence(project_id, revision.ir),
        )
        check_supported(ir, tools)
        from .handlers import verify_examples

        generated = [
            n
            for n in ir.nodes
            if tools[f"{n.tool}@{n.tool_version}"].implementation == "generated.program"
        ]
        handler_tests = {
            n.id: verify_examples(n.parameters, n.outputs["result"].format) for n in generated
        }
        has_llm = any(n.kind == "llm" for n in ir.nodes)
        if (
            sum(n.llm_policy.budget_micro_usd for n in ir.nodes if n.llm_policy)
            > policy.max_cost_micro_usd
        ):
            raise StudioError("BUDGET_EXCEEDED", "執行期模型預算超過本次核准上限。")
        compiled = lower(ir, tools)
        ir_hash = ir_fingerprint(ir)
        module = package_namespace(
            ir.workflow_id, ir_hash, has_llm=has_llm, has_program=bool(generated)
        )
        version = f"0.1.{ir.revision}"
        progress("validated", "流程與工具契約已通過檢查。")
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
        if generated:
            files[f"{module}/program_runtime.py"] = (
                Path(__file__).with_name("runtime_program.py").read_bytes()
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
        for node in generated:
            (package / f"handler_{node.id}.py").write_text(
                node.parameters["program"], encoding="utf-8", newline="\n"
            )
        if generated:
            (package / "generated-handler-tests.json").write_text(
                canonical_json(
                    {
                        "evidence_kind": "synthetic_examples_only",
                        "handlers": {
                            node.id: {
                                "examples": node.parameters["tests"],
                                "result": handler_tests[node.id],
                            }
                            for node in generated
                        },
                    }
                ),
                encoding="utf-8",
            )
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
        progress("package_written", "候選流程包已寫入，正在核對完整性。")
        verify_package(package)
        archive_path = destination / f"{module}-{version}.zip"
        with zipfile.ZipFile(archive_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(package.iterdir()):
                info = zipfile.ZipInfo(module + "/" + file.name, (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, file.read_bytes())
        mapping = {
            "ir_hash": ir_hash,
            "runtime_api": "0.2",
            "input_step": compiled["input_step"],
            "steps": [s["id"] for s in compiled["runtime_workflow"]["steps"]],
            "outputs": compiled["runtime_workflow"]["outputs"],
            "generated_handlers": handler_tests,
            "compiler_template": "2",
            "limitations": [
                "single_file_input",
                "trusted_templates_and_bounded_generated_transforms",
                "10MiB_per_file",
            ],
        }
        (destination / "mapping.json").write_text(canonical_json(mapping), encoding="utf-8")
        package_hash = digest(archive_path.read_bytes())
        with self.studio.repository.transaction() as db:
            if self.studio.projects.get(project_id).status != "active":
                raise StudioError("PROJECT_INACTIVE", "建置期間專案已封存，候選包未登錄。")
            db.execute(
                "INSERT INTO studio_builds VALUES (?,?,?,?,?,?,?,?)",
                (
                    build_id,
                    project_id,
                    revision_id,
                    destination.relative_to(base).as_posix(),
                    package_hash,
                    "candidate",
                    canonical_json(mapping),
                    now(),
                ),
            )
            EventStore(self.studio.repository).append(
                project_id,
                None,
                "generation.completed",
                {
                    "build_id": build_id,
                    "package_hash": package_hash,
                    "status": "candidate",
                },
                revision_id,
            )
            result = self.get(project_id, build_id)
        return result

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
