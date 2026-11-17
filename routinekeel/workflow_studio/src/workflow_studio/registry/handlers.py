"""Reusable generated handlers, frozen independently of the project that created them."""

import copy
import hashlib
import json

from ..compiler.handlers import validate_handler, verify_examples
from ..contracts import StudioError
from ..project.models import canonical_json
from ..project.service import now

MARKER = "studio-handler-sha256:"
MAX_TEMPLATE_BYTES = 2 * 1024 * 1024
MAX_EXPANDED_IR_BYTES = 8 * 1024 * 1024


def fingerprint(parameters):
    return hashlib.sha256(canonical_json(parameters).encode()).hexdigest()


class HandlerLibrary:
    def __init__(self, repository):
        self.repository = repository

    def get(self, tool_id, version):
        from .catalog import Catalog

        tool = Catalog(self.repository).get(tool_id, version)["tool"]
        markers = [s[len(MARKER) :] for s in tool.test_evidence if s.startswith(MARKER)]
        row = self.repository.connection.execute(
            "SELECT * FROM studio_handler_templates WHERE tool_id=? AND version=?",
            (tool_id, version),
        ).fetchone()
        if not markers and row is None:
            return None
        if len(markers) != 1 or row is None or tool.implementation != "generated.program":
            raise StudioError("TOOL_NOT_ALLOWED", "工具缺少與契約綁定的固定 Handler。")
        record = dict(row)
        if len(record["parameters_json"].encode()) > MAX_TEMPLATE_BYTES:
            raise StudioError("TOOL_NOT_ALLOWED", "固定 Handler 超過 2 MiB 儲存上限。")
        parameters = json.loads(record["parameters_json"])
        if (
            markers[0] != record["template_hash"]
            or fingerprint(parameters) != record["template_hash"]
        ):
            raise StudioError("TOOL_NOT_ALLOWED", "工具的 Handler 內容指紋不符。")
        validate_handler(parameters)
        origin = json.loads(record["origin_json"])
        if not isinstance(origin, dict) or origin.get("schema_version") != 1:
            raise StudioError("TOOL_NOT_ALLOWED", "Handler 來源紀錄版本不支援。")
        return {**record, "parameters": parameters, "origin": origin}

    def public_contract(self, tool_id, version):
        record = self.get(tool_id, version)
        if record is None:
            return None
        parameters = record["parameters"]
        return {
            "template_hash": record["template_hash"],
            "purpose": parameters["purpose"],
            "argument_types": parameters["argument_types"],
            "instructions": "This is a fixed reusable handler. Supply parameters={arguments:{...}} with optional typed overrides. Defaults are applied locally. Do not generate or modify its program, tests or contract.",
        }

    def resolve_ir(self, payload, allowed_tools=None):
        resolved = copy.deepcopy(payload)
        if not isinstance(resolved, dict) or not isinstance(resolved.get("nodes"), list):
            return resolved
        expanded_size = len(canonical_json(resolved).encode())
        for node in resolved["nodes"]:
            if (
                not isinstance(node, dict)
                or not isinstance(node.get("tool"), str)
                or not isinstance(node.get("tool_version"), str)
            ):
                continue
            if (
                allowed_tools is not None
                and f"{node['tool']}@{node['tool_version']}" not in allowed_tools
            ):
                continue
            if (
                self.repository.connection.execute(
                    "SELECT 1 FROM studio_tools WHERE tool_id=? AND version=?",
                    (node["tool"], node["tool_version"]),
                ).fetchone()
                is None
            ):
                continue
            record = self.get(node["tool"], node["tool_version"])
            if record is None:
                continue
            supplied = node.get("parameters", {})
            if not isinstance(supplied, dict) or not isinstance(
                supplied.get("arguments", {}), dict
            ):
                raise StudioError("CONTRACT_MISMATCH", "重用 Handler 只接受已宣告的參數。")
            fixed = copy.deepcopy(record["parameters"])
            if set(supplied) - {"arguments"}:
                if canonical_json(
                    {k: v for k, v in supplied.items() if k != "arguments"}
                ) != canonical_json({k: v for k, v in fixed.items() if k != "arguments"}):
                    raise StudioError(
                        "CONTRACT_MISMATCH",
                        "已註冊 Handler 的程式與測試不可改寫，請建立新工具版本。",
                    )
            fixed["arguments"].update(supplied.get("arguments", {}))
            normalized = validate_handler(fixed).model_dump()
            expanded_size += len(canonical_json(normalized).encode()) - len(
                canonical_json(supplied).encode()
            )
            if expanded_size > MAX_EXPANDED_IR_BYTES:
                raise StudioError(
                    "INPUT_INVALID",
                    "重用 Handler 展開後的流程超過 8 MiB，請精簡預設資料或減少步驟。",
                )
            node["parameters"] = normalized
        return resolved

    def validate_snapshot(self, payload, allowed_tools=None):
        if canonical_json(self.resolve_ir(payload, allowed_tools)) != canonical_json(payload):
            raise StudioError("CONTRACT_MISMATCH", "流程未保存完整的 Handler 版本快照。")

    def public_ir(self, payload):
        public = copy.deepcopy(payload)
        for node in public["nodes"]:
            record = self.get(node["tool"], node["tool_version"])
            if record is not None:
                defaults = record["parameters"]["arguments"]
                arguments = node["parameters"].get("arguments", {})
                node["parameters"] = {
                    "arguments": {
                        k: v
                        for k, v in arguments.items()
                        if k not in defaults
                        or canonical_json({"v": v}) != canonical_json({"v": defaults[k]})
                    }
                }
        return public

    def _register(self, descriptor, parameters, origin, *, actor, reason):
        from ..compiler.compatibility import tool_support
        from .catalog import Catalog

        catalog = Catalog(self.repository)
        catalog._reviewer(actor, reason, required=True)
        normalized = validate_handler(parameters).model_dump()
        if len(canonical_json(normalized).encode()) > MAX_TEMPLATE_BYTES:
            raise StudioError(
                "INPUT_INVALID", "固定 Handler 超過 2 MiB，請精簡合成測試或預設參數。"
            )
        if descriptor.implementation != "generated.program":
            raise StudioError("CONTRACT_MISMATCH", "只能把受限資料處理程式保存為可重用 Handler。")
        supported, reason_text = tool_support(descriptor)
        if not supported:
            raise StudioError("CONTRACT_MISMATCH", reason_text)
        verify_examples(normalized, descriptor.outputs["result"].format)
        template_hash = fingerprint(normalized)
        markers = [s[len(MARKER) :] for s in descriptor.test_evidence if s.startswith(MARKER)]
        if markers != [template_hash]:
            raise StudioError("CONTRACT_MISMATCH", "新工具描述必須保留固定程式的指紋。")
        with self.repository.transaction() as db:
            item = catalog.register(descriptor, actor=actor, reason=reason)
            db.execute(
                "INSERT INTO studio_handler_templates VALUES (?,?,?,?,?,?)",
                (
                    descriptor.tool_id,
                    descriptor.version,
                    template_hash,
                    canonical_json(normalized),
                    canonical_json(origin),
                    now(),
                ),
            )
            catalog._event(
                descriptor.tool_id, descriptor.version, "register_handler", actor, reason
            )
        return item

    def from_build(
        self,
        project_id,
        expected_version,
        build_id,
        expected_hash,
        node_id,
        policy,
        *,
        tool_id,
        version,
        title,
        actor,
        reason,
    ):
        from ..compiler.generator import Compiler
        from ..governance.approvals import Approvals
        from ..service import StudioService
        from ..verifier.static import verify_static
        from .models import ToolDescription

        studio = StudioService(self.repository)
        with self.repository.transaction() as db:
            project = studio.projects._editable(db, project_id, expected_version)
            build = Compiler(studio).get(project_id, build_id)
            if build["status"] != "verified" or build["package_hash"] != expected_hash:
                raise StudioError("TEST_FAILED", "請先完成獨立試做，並確認相同套件版本。")
            if project.current_revision_id != build["revision_id"]:
                raise StudioError(
                    "REVISION_CONFLICT", "目前流程已更新，請對目前版本重新試做後再保存工具。"
                )
            verify_static(studio, build, policy)
            report = next(
                (
                    json.loads(row[0])
                    for row in db.execute(
                        "SELECT report_json FROM studio_verifications WHERE project_id=? AND build_id=? ORDER BY created_at DESC",
                        (project_id, build_id),
                    )
                    if json.loads(row[0]).get("status") == "passed"
                    and json.loads(row[0]).get("package_hash") == expected_hash
                ),
                None,
            )
            if report is None:
                raise StudioError("TEST_FAILED", "缺少獨立通過報告。")
            Approvals(studio).validate_cases(project_id, report)
            revision = studio.projects.get_revision(project_id, build["revision_id"])
            node = next((n for n in revision.ir["nodes"] if n["id"] == node_id), None)
            if node is None:
                raise StudioError("INPUT_INVALID", "找不到要重用的步驟。")
            original_tool = studio.tools.get(node["tool"], node["tool_version"])["tool"]
            if original_tool.implementation != "generated.program":
                raise StudioError("INPUT_INVALID", "此入口只保存已生成的受限資料處理程式。")
            parameters = validate_handler(node["parameters"]).model_dump()
            descriptor = original_tool.model_dump()
            descriptor.update(
                tool_id=tool_id,
                version=version,
                title=title,
                test_evidence=[
                    MARKER + fingerprint(parameters),
                    "verification-sha256:" + fingerprint(report),
                ],
            )
            from pydantic import ValidationError

            try:
                descriptor = ToolDescription.model_validate(descriptor)
            except ValidationError as exc:
                raise StudioError("INPUT_INVALID", "工具識別碼、版本或名稱不合法。") from exc
            origin = {
                "schema_version": 1,
                "project_id": project_id,
                "revision_id": revision.revision_id,
                "build_id": build_id,
                "node_id": node_id,
                "package_hash": expected_hash,
                "report_id": report["report_id"],
                "report_hash": fingerprint(report),
                "verified_case_count": len(report["cases"]),
            }
            return self._register(descriptor, parameters, origin, actor=actor, reason=reason)

    def clone(self, source_tool, source_version, descriptor, *, actor, reason):
        record = self.get(source_tool, source_version)
        if record is None:
            raise StudioError("INPUT_INVALID", "來源工具沒有固定 Handler。")
        return self._register(
            descriptor,
            record["parameters"],
            {
                **record["origin"],
                "copied_from": {"tool_id": source_tool, "version": source_version},
            },
            actor=actor,
            reason=reason,
        )
