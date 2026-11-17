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
        with self.service() as studio:
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

    def edit_node(self, node_id, *, output_name=None, strip=None, arguments=None, delete=False):
        p, revision = self.project(), self.revision()
        if revision is None or "nodes" not in revision.ir:
            raise StudioError("IR_SCHEMA_INVALID", "尚無可編輯流程。")
        payload = copy.deepcopy(revision.ir)
        node = next(n for n in payload["nodes"] if n["id"] == node_id)
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
        payload["revision"] += 1
        with self.service() as studio:
            changed = studio.save_ir(p.project_id, p.version, payload, self.policy)
        self.build_id = self.approval_id = None
        return changed

    def add_case(self, input_path, expected_path, output_name=None):
        p = self.project()
        with self.service() as studio:
            one = studio.sources.import_path(p.project_id, p.version, input_path)
            two = studio.sources.import_path(
                p.project_id, studio.projects.get(p.project_id).version, expected_path
            )

            def ref(s):
                r = s.manifest["files"][0]
                return {
                    "source_id": s.source_id,
                    "version": s.version,
                    "path": r["path"],
                    "sha256": r["sha256"],
                }

            return GoldenCases(studio).save(
                p.project_id,
                studio.projects.get(p.project_id).version,
                {
                    "name": Path(input_path).name + " → " + Path(expected_path).name,
                    "input": ref(one),
                    "expected": ref(two),
                    "comparison": "exact_bytes",
                    **({"output_name": output_name} if output_name else {}),
                },
            )

    def cases(self):
        with self.service() as studio:
            return GoldenCases(studio).list(self.project_id) if self.project_id else []

    def set_case_output(self, case_id, output_name):
        p = self.project()
        with self.service() as studio:
            cases = GoldenCases(studio)
            spec = cases.get(p.project_id, case_id)["specification"]
            spec["output_name"] = output_name
            result = cases.save(p.project_id, p.version, spec, case_id=case_id)
        self.approval_id = None
        return result

    def archive_case(self, case_id):
        p = self.project()
        with self.service() as studio:
            result = GoldenCases(studio).archive(p.project_id, case_id, p.version)
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
