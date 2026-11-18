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
            result = {}
            for row in studio.tools.list(include_archived=True):
                result[row["tool_id"]] = row["tool"].title
                result[f"{row['tool_id']}@{row['version']}"] = row["tool"].title
            return result

    def tool_records(self):
        with self.service() as studio:
            records = studio.tools.list(include_archived=True)
            for item in records:
                item["compiler_supported"], item["support_reason"] = studio.tools.support(
                    item["tool"]
                )
        for item in records:
            try:
                self.policy.check(item["tool"], trust=item["trust_status"], status=item["status"])
                item["policy_reason"] = "符合目前專案政策。"
                item["available"] = item["compiler_supported"]
            except StudioError as exc:
                item["policy_reason"], item["available"] = str(exc), False
        return records

    def tool_history(self, tool_id, version):
        with self.service() as studio:
            return studio.tools.history(tool_id, version)

    def register_tool(self, payload, *, actor=None, reason=None, template_source=None):
        from pydantic import ValidationError

        from ..registry.models import ToolDescription

        try:
            tool = ToolDescription.model_validate(payload)
        except ValidationError as exc:
            raise StudioError(
                "INPUT_INVALID", "工具描述不符合契約，請檢查版本、平台、權限與輸入輸出型別。"
            ) from exc
        with self.service() as studio:
            if template_source is not None:
                return studio.handlers.clone(*template_source, tool, actor=actor, reason=reason)
            return studio.tools.register(tool, actor=actor, reason=reason)

    def handler_template(self, tool_id, version):
        with self.service() as studio:
            return studio.handlers.get(tool_id, version)

    def save_handler_tool(
        self,
        expected_version,
        build_id,
        package_hash,
        node_id,
        *,
        tool_id,
        version,
        title,
        actor,
        reason,
    ):
        with self.service() as studio:
            return studio.handlers.from_build(
                self.project_id,
                expected_version,
                build_id,
                package_hash,
                node_id,
                self.policy,
                tool_id=tool_id,
                version=version,
                title=title,
                actor=actor,
                reason=reason,
            )

    def read_tool_file(self, path):
        path = Path(path)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 128_000:
            raise StudioError("INPUT_INVALID", "工具描述須為不超過 128 KB 的一般 UTF-8 JSON 檔案。")
        try:
            raw = path.read_bytes()
            if len(raw) > 128_000:
                raise ValueError()

            def unique_pairs(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError()
                    result[key] = value
                return result

            result = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_pairs)
            if not isinstance(result, dict):
                raise ValueError()
            return result
        except (ValueError, UnicodeError) as exc:
            raise StudioError("INPUT_INVALID", "工具描述不是有效的 UTF-8 JSON。") from exc

    def decide_tool(self, item, action, actor, reason):
        with self.service() as studio:
            studio.tools._reviewer(actor, reason, required=True)
            if action == "approve":
                result = studio.tools.approve(
                    item["tool_id"],
                    item["version"],
                    item["contract_hash"],
                    actor=actor,
                    reason=reason,
                    expected_revision=item["record_version"],
                )
            elif action == "revoke":
                result = studio.tools.revoke(
                    item["tool_id"],
                    item["version"],
                    item["record_version"],
                    actor=actor,
                    reason=reason,
                )
            else:
                result = studio.tools.set_status(
                    item["tool_id"],
                    item["version"],
                    item["record_version"],
                    action,
                    actor=actor,
                    reason=reason,
                )
        self.build_id = self.approval_id = None
        self.candidates = []
        return result

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

    @property
    def provider_ready(self):
        """Expose readiness, never the session credential itself."""
        if self.provider is None:
            return False
        if isinstance(self.provider, ChatCompletionsProvider):
            import os

            return bool(os.environ.get(self.provider.api_key_env))
        return True

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
        with self.service() as studio, studio.repository.transaction():
            current = studio.projects._editable(
                studio.repository.connection, self.project_id, expected_version
            )
            criteria = [s.strip() for s in success.splitlines() if s.strip()]
            displayed = "\n".join(current.goal.get("success_criteria", []))
            if criteria == [s.strip() for s in displayed.splitlines() if s.strip()]:
                criteria = current.goal.get("success_criteria", [])
            p = studio.projects.update(
                self.project_id,
                expected_version,
                name=name,
                goal={
                    **current.goal,
                    "description": goal,
                    "success_criteria": criteria,
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

    def describe_source(self, source_id, description, expected_project):
        if expected_project is None or self.project_id != expected_project.project_id:
            raise StudioError("REVISION_CONFLICT", "專案已切換，來源說明尚未儲存。")
        with self.service() as studio:
            result = studio.sources.describe(
                expected_project.project_id, source_id, expected_project.version, description
            )
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
        if type(index) is not int or not 0 <= index < len(self.candidates):
            raise StudioError("EVIDENCE_INSUFFICIENT", "請重新選擇有效候選流程。")
        candidate = self.candidates[index]
        with self.service() as studio:
            revision = FolderDiscovery(studio).accept(
                p.project_id, p.version, candidate, self.policy
            )
        self.build_id = self.approval_id = None
        self.candidates = []
        return revision

    def folder_graphs(self, source_id=None):
        from ..discovery.evidence_graph import EvidenceGraphs

        with self.service() as studio:
            return [
                g
                for g in EvidenceGraphs(studio.repository).list(
                    self.project_id, include_archived=True
                )
                if source_id is None or g["graph"]["source_id"] == source_id
            ]

    def folder_graph(self, graph_id):
        from ..discovery.evidence_graph import EvidenceGraphs

        with self.service() as studio, studio.repository.transaction():
            graphs = EvidenceGraphs(studio.repository)
            history = graphs.history(self.project_id, graph_id)
            source = studio.sources.get(self.project_id, history[-1]["graph"]["source_id"])
            return {
                "history": history,
                "project_version": studio.projects.get(self.project_id).version,
                "source_current": source.status == "active"
                and source.version == history[-1]["graph"]["source_version"],
            }

    def decide_folder_relation(
        self, graph_id, expected_version, relation_id, action, actor, reason, *, alternative_id=None
    ):
        from ..discovery.evidence_graph import EvidenceGraphs
        from ..registry.catalog import Catalog

        Catalog._reviewer(actor, reason, required=True)
        with self.service() as studio:
            graphs = EvidenceGraphs(studio.repository)
            if action == "replace":
                result = graphs.replace_relation(
                    self.project_id,
                    graph_id,
                    expected_version,
                    relation_id,
                    alternative_id,
                    actor=actor,
                    reason=reason,
                )
            else:
                result = graphs.revise_relation(
                    self.project_id,
                    graph_id,
                    expected_version,
                    relation_id,
                    decision=action,
                    actor=actor,
                    reason=reason,
                )
        self.candidates = []
        return result

    def revise_folder_roles(self, graph_id, expected_version, roles, actor, reason):
        from ..discovery.evidence_graph import EvidenceGraphs
        from ..registry.catalog import Catalog

        Catalog._reviewer(actor, reason, required=True)
        with self.service() as studio:
            snapshot = EvidenceGraphs(studio.repository).active(self.project_id, graph_id)
            result = FolderDiscovery(studio).analyze(
                self.project_id,
                snapshot["graph"]["source_id"],
                expected_version,
                roles=roles,
                max_comparisons=snapshot["graph"]["comparison_limit"],
                graph_id=graph_id,
                actor=actor,
                reason=reason,
            )
        self.candidates = []
        return result

    def candidates_from_graph(self, graph_id):
        with self.service() as studio:
            self.candidates = FolderDiscovery(studio).candidate_irs(
                self.project_id, graph_id, self.policy
            )
        return self.candidates

    def folder_asset(self, graph_id, version, path):
        from ..discovery.evidence_graph import EvidenceGraphs

        with self.service() as studio:
            graph = EvidenceGraphs(studio.repository).get(self.project_id, graph_id, version)[
                "graph"
            ]
            source = studio.sources.get(
                self.project_id, graph["source_id"], version=graph["source_version"]
            )
            record = next((r for r in source.manifest["files"] if r["path"] == path), None)
            if record is None:
                raise StudioError("SOURCE_NOT_FOUND", "此檔案不在證據圖的來源版本。")
            return {
                "name": path,
                "preview": record["preview"],
                "content": studio.sources.content(
                    self.project_id, source.source_id, path, version=source.version
                ),
            }

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
                if (
                    self.editor_tools()
                    .get(f"{node['tool']}@{node['tool_version']}", {})
                    .get("implementation")
                    != "generated.program"
                ):
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
            return {k: tool.model_dump() for k, tool in studio.tools.available(self.policy).items()}

    def initialize_flow(
        self, expected_version, source_id, source_version, source_path, tool_key, **settings
    ):
        with self.service() as studio:
            result = studio.editor(self.policy).initialize(
                self.project_id,
                expected_version,
                source_id,
                source_version,
                source_path,
                tool_key,
                **settings,
            )
        self.build_id = self.approval_id = None
        self.candidates = []
        return result

    def node_defaults(self, tool_key, *, remaining_budget=None):
        tool = self.editor_tools().get(tool_key)
        if tool is None:
            raise StudioError("TOOL_NOT_ALLOWED", "此動作目前不可用。")
        template = self.handler_template(tool["tool_id"], tool["version"])
        if template:
            return {"parameters": {"arguments": copy.deepcopy(template["parameters"]["arguments"])}}
        if tool["implementation"] == "builtin.branch":
            return {
                "parameters": {
                    "condition": {"operator": "file_size_gt", "value": 0},
                    "when_true": {"tool": "file.copy", "version": "1.0.0", "parameters": {}},
                    "when_false": {"tool": "file.copy", "version": "1.0.0", "parameters": {}},
                }
            }
        if tool["implementation"] == "builtin.llm":
            settings = self.session_config
            amount = (
                self.policy.max_cost_micro_usd if remaining_budget is None else remaining_budget
            )
            if not settings.get("runtime_enabled") or amount <= 0:
                raise StudioError("BUDGET_EXCEEDED", "請先啟用執行期模型並配置可用預算。")
            return {
                "parameters": {
                    "endpoint": settings["endpoint"],
                    "prompt": "依照工作目標整理輸入，回傳 summary 文字欄位。",
                    "output_fields": {"summary": "string"},
                    "input_usd_per_million": settings["input_rate"],
                    "output_usd_per_million": settings["output_rate"],
                    "pricing_note": "使用者於模型設定確認的費率",
                },
                "llm_policy": {
                    "model": settings["model"],
                    "api_key": "secret://provider_key",
                    "max_calls": 1,
                    "max_output_tokens": 2000,
                    "budget_micro_usd": amount,
                    "timeout_seconds": 60,
                },
            }
        return {"parameters": {}}

    def update_node_settings(
        self,
        expected_version,
        node_id,
        *,
        parameters=None,
        llm_policy=None,
        output_path=None,
        timeout_seconds=None,
        preconditions=None,
        postconditions=None,
        cacheable=None,
    ):
        ir = self.revision().ir
        node = copy.deepcopy(next(n for n in ir["nodes"] if n["id"] == node_id))
        for key, value in (
            ("parameters", parameters),
            ("llm_policy", llm_policy),
            ("timeout_seconds", timeout_seconds),
            ("preconditions", preconditions),
            ("postconditions", postconditions),
        ):
            if value is not None:
                node[key] = value
        if (
            node["kind"] == "composite_branch"
            and isinstance(parameters, dict)
            and all(isinstance(parameters.get(side), dict) for side in ("when_true", "when_false"))
        ):
            from ..ir.references import parse_reference

            reference = parse_reference(node["inputs"]["source"]["source"])
            actual_port = (
                ir["inputs"][reference.owner]
                if reference.scheme == "input"
                else next(n for n in ir["nodes"] if n["id"] == reference.owner)["outputs"][
                    reference.port
                ]
            )
            node["inputs"]["source"]["port"] = {**actual_port, "path": None}
            allowed = self.editor_tools()
            arms = [
                allowed.get(
                    f"{parameters.get(side, {}).get('tool')}@{parameters.get(side, {}).get('version')}"
                )
                for side in ("when_true", "when_false")
            ]
            if all(arms) and arms[0]["outputs"] == arms[1]["outputs"]:
                for key, port in arms[0]["outputs"].items():
                    path = node["outputs"][key]["path"]
                    node["outputs"][key] = {**port, "path": path}
        if output_path is not None:
            node["outputs"]["result"]["path"] = output_path
        if cacheable is not None:
            node["cache_policy"] = {
                "mode": "content_addressed" if cacheable else "none",
                "fingerprint_fields": ["inputs", "parameters", "tool_version"] if cacheable else [],
            }
        return self.edit_graph(expected_version, upsert_nodes=[node])

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

    def attach_machine_evidence(self, expected_version, report_id):
        from ..governance.machine_evidence import MachineEvidence

        with self.service() as studio:
            result = MachineEvidence(studio.repository).attach(
                studio, self.project_id, expected_version, report_id, self.policy
            )
        self.build_id = self.approval_id = None
        self.candidates = []
        return result

    def machine_evidence_details(self, identity):
        from ..governance.machine_evidence import MachineEvidence

        with self.service() as studio:
            return MachineEvidence(studio.repository).inspect(
                self.project_id, identity, self.revision().ir
            )

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

    def case_assets(self, case_id, version, *, current=()):
        with self.service() as studio:
            spec = GoldenCases(studio).get(self.project_id, case_id, version)["specification"]
            assets = {}
            for side in ("input", "expected"):
                ref = spec[side]
                source = studio.sources.get(
                    self.project_id,
                    ref["source_id"],
                    version=None if side in current else ref["version"],
                )
                if side in current and source.status != "active":
                    raise StudioError("SOURCE_REMOVED", "來源已移除，請選擇替換檔案。")
                record = next(
                    (r for r in source.manifest["files"] if r["path"] == ref["path"]), None
                )
                if record is None and side in current and len(source.manifest["files"]) == 1:
                    record = source.manifest["files"][0]
                if record is None:
                    raise StudioError(
                        "INPUT_INVALID", "目前來源有多個不同檔案，請明確選擇替換檔案。"
                    )
                if side not in current and record["sha256"] != ref["sha256"]:
                    raise StudioError("SOURCE_CORRUPT", "案例來源指紋不符。")
                assets[side] = {
                    "name": f"{record['path']} · 來源第 {source.version} 版",
                    "preview": record["preview"],
                    "content": studio.sources.content(
                        self.project_id, ref["source_id"], record["path"], version=source.version
                    ),
                    "ref": {
                        "source_id": source.source_id,
                        "version": source.version,
                        "path": record["path"],
                        "sha256": record["sha256"],
                    },
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
        use_current_input=False,
        use_current_expected=False,
    ):
        with self.service() as studio, studio.repository.transaction() as db:
            studio.projects._editable(db, self.project_id, expected_version)
            catalog = GoldenCases(studio)
            spec = catalog.get(self.project_id, case_id, source_version)["specification"]
            current_sides = [
                side
                for side, enabled in (
                    ("input", use_current_input),
                    ("expected", use_current_expected),
                )
                if enabled
            ]
            if current_sides:
                # Use this connection: the surrounding write transaction owns the version check.
                for side in current_sides:
                    ref = spec[side]
                    source = studio.sources.get(self.project_id, ref["source_id"])
                    if source.status != "active":
                        raise StudioError("SOURCE_REMOVED", "來源已移除，請選擇替換檔案。")
                    record = next(
                        (r for r in source.manifest["files"] if r["path"] == ref["path"]), None
                    )
                    if record is None and len(source.manifest["files"]) == 1:
                        record = source.manifest["files"][0]
                    if record is None:
                        raise StudioError("INPUT_INVALID", "請明確選擇來源中要使用的檔案。")
                    spec[side] = {
                        "source_id": source.source_id,
                        "version": source.version,
                        "path": record["path"],
                        "sha256": record["sha256"],
                    }
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

    def inspect_release(self, release_id):
        from ..governance.recovery import ReleaseRecovery

        with self.service() as studio:
            return ReleaseRecovery(studio).inspect(self.project_id, release_id)

    def reconcile_release(self, release_id, expected_observation, actor, reason):
        from ..governance.recovery import ReleaseRecovery

        with self.service() as studio:
            result = ReleaseRecovery(studio).reconcile(
                self.project_id, release_id, expected_observation, actor=actor, reason=reason
            )
        self.approval_id = None
        return result

    def build_history(self, include_archived=False):
        with self.service() as studio:
            return studio.reviews(self.policy).list(
                self.project_id, include_archived=include_archived
            )

    def build_details(self, build_id):
        with self.service() as studio:
            return studio.reviews(self.policy).details(self.project_id, build_id)

    def build_file(self, build_id, filename):
        import re

        if filename not in {
            "README.md",
            "PROVENANCE.md",
            "workflow.ir.json",
            "generated-handler-tests.json",
        } and not re.fullmatch(r"handler_[a-z][a-z0-9_-]{0,31}\.py", filename):
            raise StudioError("INPUT_INVALID", "這不是可預覽的建置文字檔。")
        with self.service() as studio:
            build = Compiler(studio).get(self.project_id, build_id)
            root = studio.repository.root.resolve()
            path = build["path"] / "package" / filename
            if not path.resolve().is_relative_to(root) or any(
                p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
                for p in (path, *path.parents)
                if p != root and p.is_relative_to(root)
            ):
                raise StudioError("INPUT_INVALID", "建置預覽位置不安全。")
            with path.open("rb") as stream:
                data = stream.read(128_001)
            return data[:128_000].decode("utf-8", errors="replace") + (
                "\n（預覽截斷，完整檔案保留於建置目錄。）" if len(data) > 128_000 else ""
            )

    def review_build(
        self, expected_version, build_id, package_hash, sequence, action, actor, reason
    ):
        with self.service() as studio:
            result = studio.reviews(self.policy).decide(
                self.project_id,
                expected_version,
                build_id,
                package_hash,
                sequence,
                action,
                actor=actor,
                reason=reason,
            )
        self.build_id = self.approval_id = None
        return result

    def revoke_build_approval(self, approval_id, actor, reason):
        with self.service() as studio:
            result = Approvals(studio).revoke(
                self.project_id, approval_id, actor=actor, reason=reason
            )
        self.approval_id = None
        return result

    def select_build(self, build_id):
        from ..governance.reviews import ReviewStore

        with self.service() as studio:
            build = Compiler(studio).get(self.project_id, build_id)
            ReviewStore(studio.repository).assert_open(self.project_id, build_id)
            if studio.projects.get(self.project_id).current_revision_id != build["revision_id"]:
                raise StudioError(
                    "REVISION_CONFLICT", "這是歷史流程的建置；請先從歷史版本另存續編並試做。"
                )
        self.build_id, self.approval_id = build_id, None
        return self.build_details(build_id)

    def project_events(self, after=0):
        from ..llm.streaming import EventStore

        with self.service() as studio:
            return EventStore(studio.repository).read(self.project_id, after=after, limit=50)

    def rollback(self, release_id):
        with self.service() as studio:
            return Releases(studio).rollback(self.project_id, release_id, self.policy)

    def disable_release(self, release_id, actor, reason):
        with self.service() as studio:
            row = next(
                (
                    r
                    for r in Releases(studio).history(self.project_id)
                    if r["release_id"] == release_id
                ),
                None,
            )
            if row is None:
                raise StudioError("PUBLISH_FAILED", "找不到此專案的發布紀錄。")
            payload = json.loads(row["payload_json"])
            return Releases(studio).disable(
                self.project_id,
                row["target_root"],
                payload["workflow_id"],
                release_id,
                actor=actor,
                reason=reason,
            )

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
        image_input_token_bound=65536,
        persist=True,
    ):
        import hashlib

        if (
            type(image_input_token_bound) is not int
            or not 1 <= image_input_token_bound <= 10_000_000
        ):
            raise StudioError("INPUT_INVALID", "圖片用量預留必須是 1–10,000,000 的整數。")

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
            "image_input_token_bound": image_input_token_bound,
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

    def analysis_disclosure(self, prepared):
        from ..discovery.images import (
            DEFAULT_IMAGE_INPUT_TOKENS,
            context_from_messages,
            input_reservation,
        )

        messages = json.loads(prepared.messages_json)
        context = context_from_messages(messages)
        image_tokens = context.get("image_policy", {}).get(
            "input_tokens_reserved_per_image", DEFAULT_IMAGE_INPUT_TOKENS
        )
        return {
            "sources": context["sources"],
            "image_tokens": image_tokens,
            "reserved_micro_usd": self.budget.cost(
                input_reservation(messages, image_tokens), self.budget.max_output_tokens
            ),
        }

    def analysis_asset(self, prepared, index):
        import base64

        from ..discovery.images import context_from_messages

        messages = json.loads(prepared.messages_json)
        item = context_from_messages(messages)["sources"][index]
        if item.get("kind") == "image":
            part = messages[1]["content"][item["image_index"]]
            content = base64.b64decode(part["image_url"]["url"].split(",", 1)[1], validate=True)
            return {
                "name": item["path"],
                "content": content,
                "preview": {"kind": "image", **item["transmission"]},
            }
        return {
            "name": item["path"],
            "content": item["content"].encode(),
            "preview": {"kind": "text"},
        }


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
