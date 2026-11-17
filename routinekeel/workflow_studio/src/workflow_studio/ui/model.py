import copy
import json
import platform
import sys
from pathlib import Path

from ..compiler.generator import Compiler
from ..contracts import StudioError
from ..discovery.folder import FolderDiscovery
from ..governance.approvals import Approvals
from ..governance.release import Releases
from ..llm.provider import ChatCompletionsProvider
from ..llm.usage import Budget
from ..registry.policy import ToolPolicy
from ..service import StudioService
from ..verifier.golden_cases import GoldenCases


class StudioUIModel:
    def __init__(self, workspace, *, runtime_python=None):
        self.workspace = Path(workspace).resolve()
        self.runtime_python = runtime_python or self._find_runtime()
        self.project_id = self.build_id = self.approval_id = None
        self.candidates = []
        self.provider = self.budget = None
        self.session_config = {}
        system = {"Windows": "windows", "Darwin": "macos"}.get(platform.system(), "linux")
        self.policy = ToolPolicy(system)
        with self.service() as studio:
            studio.tools.seed()

    def _find_runtime(self):
        executable = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        for base in (
            self.workspace / ".venv",
            self.workspace / "flow_base/.venv",
            self.workspace / "routinekeel/flow_base/.venv",
        ):
            if (base / executable).is_file():
                return base / executable
        return sys.executable

    def service(self):
        return StudioService.open_workspace(self.workspace)

    def projects(self, include_archived=False):
        with self.service() as studio:
            return studio.projects.list(include_archived=include_archived)

    def tool_titles(self):
        with self.service() as studio:
            return {row["tool_id"]: row["tool"].title for row in studio.tools.list()}

    def select_project(self, project_id):
        self.project_id, self.build_id, self.approval_id = project_id, None, None
        self.candidates = []
        with self.service() as studio:
            saved = studio.projects.settings(project_id)
        self.provider = self.budget = None
        if saved:
            self.session_config = {}
            self.configure_provider(**saved, persist=False)
        else:
            self.session_config = {}
            self.policy = ToolPolicy(self.policy.platform)
        return self.project()

    def project(self):
        if not self.project_id:
            raise StudioError("PROJECT_NOT_FOUND", "請先建立或選擇工作專案。")
        with self.service() as studio:
            return studio.projects.get(self.project_id)

    def create_project(self, name, goal, success):
        with self.service() as studio:
            p = studio.projects.create(
                name,
                {
                    "description": goal,
                    "success_criteria": [s.strip() for s in success.splitlines() if s.strip()],
                },
            )
            if self.session_config:
                studio.projects.update_settings(p.project_id, p.version, self.session_config)
        return self.select_project(p.project_id)

    def update_project(self, name, goal, success, expected_version):
        with self.service() as studio:
            p = studio.projects.update(
                self.project_id,
                expected_version,
                name=name,
                goal={
                    "description": goal,
                    "success_criteria": [s.strip() for s in success.splitlines() if s.strip()],
                },
            )
        self.build_id = self.approval_id = None
        self.candidates = []
        return p

    def archive(self, restore=False):
        p = self.project()
        with self.service() as studio:
            return (studio.projects.restore if restore else studio.projects.archive)(
                p.project_id, p.version
            )

    def import_path(self, path, source_id=None):
        p = self.project()
        with self.service() as studio:
            result = studio.sources.import_path(p.project_id, p.version, path, source_id=source_id)
        self.build_id = self.approval_id = None
        self.candidates = []
        return result

    def import_text(self, text, name="工作說明.txt"):
        p = self.project()
        with self.service() as studio:
            result = studio.sources.import_text(p.project_id, p.version, text, name=name)
        self.build_id = self.approval_id = None
        self.candidates = []
        return result

    def remove_source(self, source_id):
        p = self.project()
        with self.service() as studio:
            result = studio.sources.remove(p.project_id, source_id, p.version)
        self.build_id = self.approval_id = None
        self.candidates = []
        return result

    def sources(self):
        with self.service() as studio:
            return studio.sources.list(self.project_id) if self.project_id else []

    def content(self, source_id, relative):
        with self.service() as studio:
            return studio.sources.content(self.project_id, source_id, relative)

    def folder_candidates(self, source_id):
        p = self.project()
        with self.service() as studio:
            discovery = FolderDiscovery(studio)
            graph = discovery.analyze(p.project_id, source_id, p.version)
            self.candidates = discovery.candidate_irs(p.project_id, graph["graph_id"], self.policy)
            return self.candidates, graph["graph"]["unknowns"]

    def accept_candidate(self, index):
        p = self.project()
        candidate = self.candidates[index]
        with self.service() as studio, studio.repository.transaction():
            revision = studio.save_ir(p.project_id, p.version, candidate["ir"], self.policy)
            GoldenCases(studio).from_candidate(
                p.project_id, studio.projects.get(p.project_id).version, candidate
            )
        self.build_id = self.approval_id = None
        return revision

    def revision(self):
        p = self.project()
        if not p.current_revision_id:
            return None
        with self.service() as studio:
            return studio.projects.get_revision(p.project_id, p.current_revision_id)

    def edit_node(
        self,
        node_id,
        *,
        output_name=None,
        strip=None,
        arguments=None,
        delete=False,
        expected_version=None,
    ):
        p, revision = self.project(), self.revision()
        if revision is None or "nodes" not in revision.ir:
            raise StudioError("IR_SCHEMA_INVALID", "尚無可編輯流程。")
        payload = copy.deepcopy(revision.ir)
        node = next((n for n in payload["nodes"] if n["id"] == node_id), None)
        if node is None:
            raise StudioError("INPUT_INVALID", "此步驟已不存在，請重新讀取流程。")
        if delete:
            if any(node_id in n["depends_on"] for n in payload["nodes"]) or any(
                v.startswith(f"artifact://{node_id}/") for v in payload["outputs"].values()
            ):
                raise StudioError(
                    "IR_SCHEMA_INVALID", "其他步驟或交付物仍使用此步驟，不能直接刪除。"
                )
            payload["nodes"] = [n for n in payload["nodes"] if n["id"] != node_id]
        else:
            if output_name:
                node["outputs"]["result"]["path"] = "artifacts/" + output_name
            if strip is not None:
                if node["tool"] != "text.normalize":
                    raise StudioError("INPUT_INVALID", "這個步驟沒有文字空白設定。")
                node["parameters"]["strip"] = strip
            if arguments is not None:
                if not node["tool"].startswith("data.transform_"):
                    raise StudioError("INPUT_INVALID", "此步驟沒有生成 Handler 的設定欄位。")
                from ..compiler.handlers import validate_handler

                node["parameters"]["arguments"] = arguments
                validate_handler(node["parameters"])
        return self.edit_graph(
            p.version if expected_version is None else expected_version,
            upsert_nodes=[] if delete else [node],
            delete_nodes=[node_id] if delete else [],
        )

    def add_case(self, input_path, expected_path, output_name=None, *, comparison="exact_bytes"):
        p = self.project()
        if not Path(input_path).is_file() or not Path(expected_path).is_file():
            raise StudioError("INPUT_INVALID", "每個案例請選擇單一輸入檔及正確成果檔。")
        with self.service() as studio, studio.repository.transaction():
            one = studio.sources.import_path(p.project_id, p.version, input_path)
            two = studio.sources.import_path(
                p.project_id, studio.projects.get(p.project_id).version, expected_path
            )

            def ref(s):
                if len(s.manifest["files"]) != 1:
                    raise StudioError(
                        "INPUT_INVALID", "每個案例來源必須只有一個檔案，不能只取壓縮包的第一項。"
                    )
                r = s.manifest["files"][0]
                return {
                    "source_id": s.source_id,
                    "version": s.version,
                    "path": r["path"],
                    "sha256": r["sha256"],
                }

            result = GoldenCases(studio).save(
                p.project_id,
                studio.projects.get(p.project_id).version,
                {
                    "name": Path(input_path).name + " → " + Path(expected_path).name,
                    "input": ref(one),
                    "expected": ref(two),
                    "comparison": comparison,
                    **({"output_name": output_name} if output_name else {}),
                },
            )
        self.approval_id = None
        return result

    def editor_tools(self):
        with self.service() as studio:
            return {k: tool.model_dump() for k, tool in studio.tools.allowed(self.policy).items()}

    def evidence_sources(self):
        revision = self.revision()
        with self.service() as studio:
            sources = {(s.source_id, s.version): s for s in studio.sources.list(self.project_id)}
            for evidence in revision.ir.get("evidence", []) if revision else []:
                key = (evidence["source_id"], evidence["source_version"])
                if key not in sources:
                    sources[key] = studio.sources.get(self.project_id, key[0], version=key[1])
            return [
                {"source_id": s.source_id, "version": s.version, "name": s.name, **record}
                for s in sources.values()
                for record in s.manifest["files"]
            ]

    def evidence_decisions(self):
        with self.service() as studio:
            return studio.evidence(self.policy).records.list(self.project_id)

    def evidence_content(self, source_id, version, path):
        with self.service() as studio:
            return studio.sources.content(self.project_id, source_id, path, version=version)

    def edit_evidence(self, expected_version, evidence, node_ids):
        with self.service() as studio:
            result = studio.evidence(self.policy).edit(
                self.project_id, expected_version, evidence, node_ids=node_ids
            )
        self.build_id = self.approval_id = None
        self.candidates = []
        return result

    def decide_evidence(self, expected_version, evidence_id, actor, reason, *, revoke=False):
        with self.service() as studio:
            result = studio.evidence(self.policy).decide(
                self.project_id,
                expected_version,
                evidence_id,
                actor=actor,
                reason=reason,
                revoke=revoke,
            )
        self.build_id = self.approval_id = None
        return result

    def remove_evidence(self, expected_version, evidence_id):
        with self.service() as studio:
            result = studio.evidence(self.policy).remove(
                self.project_id, expected_version, evidence_id
            )
        self.build_id = self.approval_id = None
        return result

    def node_template(
        self,
        node_id,
        tool_key,
        source,
        path,
        *,
        parameters=None,
        evidence_refs=None,
        llm_policy=None,
    ):
        with self.service() as studio:
            return studio.editor(self.policy).template(
                self.project_id,
                node_id,
                tool_key,
                {"source": source},
                {"result": path},
                parameters=parameters,
                evidence_refs=evidence_refs,
                llm_policy=llm_policy,
            )

    def edit_graph(self, expected_version, **changes):
        with self.service() as studio:
            result = studio.editor(self.policy).apply(self.project_id, expected_version, **changes)
        self.build_id = self.approval_id = None
        self.candidates = []
        return result

    def cases(self, include_archived=False):
        with self.service() as studio:
            return (
                GoldenCases(studio).list(self.project_id, include_archived)
                if self.project_id
                else []
            )

    def case_history(self, case_id):
        with self.service() as studio:
            return GoldenCases(studio).history(self.project_id, case_id)

    def case_preview(self, case_id, version):
        with self.service() as studio:
            case = GoldenCases(studio).get(self.project_id, case_id, version)
            preview = {}
            for side in ("input", "expected"):
                ref = case["specification"][side]
                raw = studio.sources.content(
                    self.project_id, ref["source_id"], ref["path"], version=ref["version"]
                )
                try:
                    text = raw.decode("utf-8")[:16000]
                except UnicodeDecodeError:
                    text = "此為二進位資料，請在來源預覽查看原始內容。"
                preview[side] = f"{ref['path']} · {len(raw)} bytes\n{text}" + (
                    "\n（預覽已截斷）" if len(raw) > 16000 else ""
                )
            return preview

    def case_assets(self, case_id, version):
        with self.service() as studio:
            spec = GoldenCases(studio).get(self.project_id, case_id, version)["specification"]
            assets = {}
            for side in ("input", "expected"):
                ref = spec[side]
                source = studio.sources.get(
                    self.project_id, ref["source_id"], version=ref["version"]
                )
                record = next(r for r in source.manifest["files"] if r["path"] == ref["path"])
                assets[side] = {
                    "name": ref["path"],
                    "preview": record["preview"],
                    "content": studio.sources.content(
                        self.project_id, ref["source_id"], ref["path"], version=ref["version"]
                    ),
                }
            return assets

    def update_case(
        self,
        case_id,
        source_version,
        expected_version,
        *,
        name,
        comparison,
        output_name=None,
        input_path=None,
        expected_path=None,
    ):
        with self.service() as studio, studio.repository.transaction() as db:
            studio.projects._editable(db, self.project_id, expected_version)
            catalog = GoldenCases(studio)
            spec = catalog.get(self.project_id, case_id, source_version)["specification"]
            spec.update(name=name, comparison=comparison)
            if output_name:
                spec["output_name"] = output_name
            else:
                spec.pop("output_name", None)
            for side, path in (("input", input_path), ("expected", expected_path)):
                if path is None:
                    continue
                if not Path(path).is_file():
                    raise StudioError("INPUT_INVALID", "請選擇單一案例檔案。")
                source = studio.sources.import_path(
                    self.project_id, studio.projects.get(self.project_id).version, path
                )
                if len(source.manifest["files"]) != 1:
                    raise StudioError("INPUT_INVALID", "每個案例來源必須只有一個檔案。")
                record = source.manifest["files"][0]
                spec[side] = {
                    "source_id": source.source_id,
                    "version": source.version,
                    "path": record["path"],
                    "sha256": record["sha256"],
                }
            result = catalog.save(
                self.project_id, studio.projects.get(self.project_id).version, spec, case_id=case_id
            )
        self.approval_id = None
        return result

    def revision_history(self):
        with self.service() as studio:
            return studio.projects.revisions(self.project_id, include_discarded=True)

    def compare_revisions(self, before_id, after_id):
        with self.service() as studio:
            return studio.history.compare(self.project_id, before_id, after_id)

    def reuse_revision(self, revision_id, expected_version, *, restore_goal=False):
        with self.service() as studio:
            result = studio.history.reuse(
                self.project_id,
                revision_id,
                expected_version,
                self.policy,
                restore_goal=restore_goal,
            )
        self.build_id = self.approval_id = None
        self.candidates = []
        return result

    def discard_revision(self, revision_id, expected_version):
        with self.service() as studio:
            result = studio.history.discard(self.project_id, revision_id, expected_version)
        self.build_id = self.approval_id = None
        self.candidates = []
        return result

    def set_case_output(self, case_id, output_name):
        p = self.project()
        with self.service() as studio:
            cases = GoldenCases(studio)
            spec = cases.get(p.project_id, case_id)["specification"]
            spec["output_name"] = output_name
            result = cases.save(p.project_id, p.version, spec, case_id=case_id)
        self.approval_id = None
        return result

    def archive_case(self, case_id, expected_version=None):
        p = self.project()
        with self.service() as studio:
            result = GoldenCases(studio).archive(
                p.project_id, case_id, p.version if expected_version is None else expected_version
            )
        self.approval_id = None
        return result

    def compile(self):
        revision = self.revision()
        if not revision:
            raise StudioError("IR_SCHEMA_INVALID", "請先整理並採用候選流程。")
        with self.service() as studio:
            build = studio.compile(self.project_id, revision.revision_id, self.policy)
        self.build_id, self.approval_id = build["build_id"], None
        return build

    def verification_bindings(self):
        if not self.build_id:
            self.compile()
        cases = self.cases()
        if not cases:
            raise StudioError("TEST_FAILED", "請先選取試做輸入與正確成果樣本。")
        outputs = list(self.revision().ir["outputs"])
        bindings = [
            {
                "case_id": c["case_id"],
                "version": c["version"],
                "output": c["specification"].get(
                    "output_name", outputs[0] if len(outputs) == 1 else None
                ),
            }
            for c in cases
        ]
        if any(b["output"] is None for b in bindings):
            raise StudioError("TEST_FAILED", "請在案例對應中選擇每個案例要核對的交付物。")
        return bindings

    def prepare_verification(self):
        from ..verifier.authorization import disclosure

        bindings = self.verification_bindings()
        with self.service() as studio:
            return disclosure(studio, self.project_id, self.build_id, bindings)

    def authorize_verification(self, prepared):
        import os

        from ..verifier.authorization import RuntimeAuthorization

        if self.provider is None or any(
            item["endpoint"] != self.provider.destination
            or item["model"] != self.provider.model
            or item["secret"] != "secret://provider_key"
            for item in prepared["models"]
        ):
            raise StudioError("APPROVAL_REQUIRED", "候選模型或目的端與已設定憑證不符，未傳送金鑰。")
        key = os.environ.get(self.provider.api_key_env)
        if not key:
            raise StudioError("PROVIDER_UNAVAILABLE", "請先設定此模型的金鑰。")
        return RuntimeAuthorization(prepared["request_hash"], {"provider_key": key})

    def verify(self, *, authorization=None):
        bindings = self.verification_bindings()
        with self.service() as studio:
            return studio.verify(
                self.project_id,
                self.build_id,
                bindings,
                self.policy,
                runtime_python=self.runtime_python,
                authorization=authorization,
            )

    def approve_publish(self, target, actor, reason):
        if not self.build_id:
            raise StudioError("TEST_FAILED", "請先完成試做。")
        with self.service() as studio:
            build = Compiler(studio).get(self.project_id, self.build_id)
            approval = Approvals(studio).approve(
                self.project_id,
                self.build_id,
                build["package_hash"],
                self.policy,
                actor=actor,
                reason=reason,
                external_authorized=self.policy.allow_external_effects,
            )
            releases = Releases(studio)
            workflow_id = studio.projects.get_revision(self.project_id, build["revision_id"]).ir[
                "workflow_id"
            ]
            current = releases.current(target, workflow_id)
            result = releases.publish(
                self.project_id,
                approval["approval_id"],
                target,
                self.policy,
                expected_current=current["release_id"] if current else None,
            )
            self.approval_id = approval["approval_id"]
            return result

    def prepare_repair(self, report_id):
        if self.provider is None or self.budget is None:
            raise StudioError("PROVIDER_UNAVAILABLE", "請先設定修復模型、費率與預算。")
        bindings = self.verification_bindings()
        with self.service() as studio:
            return studio.model_repair(
                self.provider, self.budget, self.policy, runtime_python=self.runtime_python
            ).prepare(self.project_id, report_id, bindings)

    def repair_model(self, prepared, callback, cancelled, *, authorized_hash, runtime_secrets=None):
        self.approval_id = None
        final = None
        with self.service() as studio:
            for event in studio.model_repair(
                self.provider, self.budget, self.policy, runtime_python=self.runtime_python
            ).run(
                prepared,
                authorized_hash=authorized_hash,
                runtime_secrets=runtime_secrets,
                cancelled=cancelled,
            ):
                if event["event"] == "repair.proposed":
                    self.build_id = None
                if event["event"] in {"repair.verified", "repair.completed"}:
                    self.build_id = event["build_id"]
                if event["event"] == "repair.completed":
                    from ..verifier.report import Verifier

                    final = Verifier(studio).get(self.project_id, event["report_id"])
                callback(event)
        return final

    def release_history(self):
        with self.service() as studio:
            return Releases(studio).history(self.project_id) if self.project_id else []

    def rollback(self, release_id):
        with self.service() as studio:
            return Releases(studio).rollback(self.project_id, release_id, self.policy)

    def configure_provider(
        self,
        endpoint,
        model,
        budget_usd,
        input_rate,
        output_rate,
        *,
        runtime_enabled=False,
        runtime_budget_usd=0.0,
        persist=True,
    ):
        import hashlib

        key_env = (
            "WORKFLOW_STUDIO_SESSION_KEY_"
            + hashlib.sha256(endpoint.encode()).hexdigest()[:16].upper()
        )
        provider = ChatCompletionsProvider(endpoint, model, api_key_env=key_env)
        budget = Budget(
            20,
            int(budget_usd * 1_000_000),
            8000,
            int(input_rate * 1_000_000),
            int(output_rate * 1_000_000),
        )
        configuration = {
            "endpoint": endpoint,
            "model": model,
            "budget_usd": budget_usd,
            "input_rate": input_rate,
            "output_rate": output_rate,
            "runtime_enabled": runtime_enabled,
            "runtime_budget_usd": runtime_budget_usd,
        }
        if runtime_enabled and runtime_budget_usd <= 0:
            raise StudioError("INPUT_INVALID", "啟用執行期模型前，請設定正數預算。")
        policy = ToolPolicy(
            self.policy.platform,
            frozenset({"workspace.write", "model.call", "secret.read"})
            if runtime_enabled
            else frozenset({"workspace.write"}),
            runtime_enabled,
            runtime_enabled,
            int(runtime_budget_usd * 1_000_000) if runtime_enabled else 0,
        )
        if persist and self.project_id:
            p = self.project()
            with self.service() as studio:
                studio.projects.update_settings(p.project_id, p.version, configuration)
        self.provider, self.budget = provider, budget
        self.session_config, self.policy = configuration, policy

    def prepare_analysis(self, source_ids):
        if self.provider is None or self.budget is None:
            raise StudioError("PROVIDER_UNAVAILABLE", "請先設定可用模型、確認費率與預算。")
        with self.service() as studio:
            return studio.discovery(self.provider, self.budget, self.policy).prepare(
                self.project_id, source_ids
            )

    def analyze(self, prepared, callback, cancelled):
        with self.service() as studio:
            for event in studio.discovery(self.provider, self.budget, self.policy).run(
                prepared, authorized_hash=prepared.request_hash, cancelled=cancelled
            ):
                callback(event)
        self.build_id = self.approval_id = None


def analysis_text(raw):
    import re

    match = re.search(r'"analysis"\s*:\s*"((?:\\.|[^"\\])*)', raw)
    if not match:
        return ""
    fragment = match[1]
    for trim in range(7):
        try:
            return json.loads('"' + (fragment[:-trim] if trim else fragment) + '"')
        except ValueError:
            continue
    return ""
