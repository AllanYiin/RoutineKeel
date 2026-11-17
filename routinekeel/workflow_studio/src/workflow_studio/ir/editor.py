"""Atomic, versioned graph edits using the same contracts as generated workflows."""

import copy

from ..compiler.compatibility import check_supported
from ..contracts import StudioError
from ..llm.streaming import EventStore
from .references import parse_reference
from .validator import validate_ir


class FlowEditor:
    def __init__(self, studio, policy):
        self.studio, self.policy = studio, policy

    def current(self, project_id):
        project = self.studio.projects.get(project_id)
        if not project.current_revision_id:
            raise StudioError("IR_SCHEMA_INVALID", "請先產生並採用流程。")
        revision = self.studio.projects.get_revision(project_id, project.current_revision_id)
        if (
            revision.ir.get("ir_version") != "1.0"
            or revision.discarded
            or revision.status not in {"ir_ready", "verified", "approved", "published"}
        ):
            raise StudioError("IR_SCHEMA_INVALID", "目前草稿尚未形成可編輯流程。")
        if revision.ir["goal"] != project.goal:
            raise StudioError("REVISION_CONFLICT", "工作目標已改變，請先重新分析或明確還原目標。")
        return revision.ir

    def template(
        self,
        project_id,
        node_id,
        tool_key,
        bindings,
        paths,
        *,
        parameters=None,
        evidence_refs=None,
        llm_policy=None,
    ):
        ir = self.current(project_id)
        tool = self.studio.tools.allowed(self.policy).get(tool_key)
        if tool is None:
            raise StudioError("TOOL_NOT_ALLOWED", "工具不可用或尚未核准。")
        if set(bindings) != set(tool.inputs) or set(paths) != set(tool.outputs):
            raise StudioError("CONTRACT_MISMATCH", "請完整指定工具的輸入與輸出。")
        dependencies = set()
        for value in bindings.values():
            ref = parse_reference(value)
            if ref.scheme == "artifact":
                dependencies.add(ref.owner)
        external = tool.side_effects == "external"
        return {
            "id": node_id,
            "kind": tool.kind,
            "tool": tool.tool_id,
            "tool_version": tool.version,
            "inputs": {
                k: {"source": bindings[k], "port": port.model_dump()}
                for k, port in tool.inputs.items()
            },
            "outputs": {
                k: {**port.model_dump(), "path": paths[k]} for k, port in tool.outputs.items()
            },
            "depends_on": sorted(dependencies),
            "parameters": copy.deepcopy(parameters or {}),
            "preconditions": [{"operator": "exists", "reference": v} for v in bindings.values()],
            "postconditions": [
                {"operator": "exists", "reference": f"artifact://{node_id}/{k}"}
                for k in tool.outputs
            ],
            "side_effects": tool.side_effects,
            "retry_policy": {"mode": "never", "max_attempts": 1},
            "cache_policy": {
                "mode": "none" if external else "content_addressed",
                "fingerprint_fields": [] if external else ["inputs", "parameters", "tool_version"],
            },
            "platform_requirements": list(tool.platforms),
            "resources": tool.resources.model_dump(),
            "timeout_seconds": 60,
            "evidence_refs": list(evidence_refs or [e["id"] for e in ir["evidence"]]),
            "llm_policy": copy.deepcopy(llm_policy),
        }

    def apply(
        self,
        project_id,
        expected_version,
        *,
        upsert_nodes=(),
        delete_nodes=(),
        outputs=None,
        inputs=None,
    ):
        with self.studio.repository.transaction() as db:
            project = self.studio.projects._editable(db, project_id, expected_version)
            payload = copy.deepcopy(self.current(project_id))
            nodes = {node["id"]: node for node in payload["nodes"]}
            if not isinstance(upsert_nodes, (list, tuple)) or not isinstance(
                delete_nodes, (list, tuple)
            ):
                raise StudioError("INPUT_INVALID", "步驟修改與刪除必須是清單。")
            replacements = list(upsert_nodes)
            if not all(
                isinstance(n, dict) and isinstance(n.get("id"), str) for n in replacements
            ) or not all(isinstance(n, str) for n in delete_nodes):
                raise StudioError("INPUT_INVALID", "每個步驟修改必須有有效的識別碼。")
            ids = [n["id"] for n in replacements]
            if len(set(ids)) != len(ids) or set(ids) & set(delete_nodes):
                raise StudioError("INPUT_INVALID", "同一筆編輯不能重複修改或同時刪除同一個步驟。")
            if not set(delete_nodes) <= nodes.keys():
                raise StudioError("INPUT_INVALID", "找不到要刪除的步驟。")
            for identity in delete_nodes:
                del nodes[identity]
            for node in replacements:
                nodes[node["id"]] = copy.deepcopy(node)
            payload["nodes"] = list(nodes.values())
            if outputs is not None:
                payload["outputs"] = copy.deepcopy(outputs)
            if inputs is not None:
                payload["inputs"] = copy.deepcopy(inputs)
            ordinals = [
                r.ir["revision"]
                for r in self.studio.projects.revisions(project_id, include_discarded=True)
                if r.status != "draft" and r.ir.get("ir_version") == "1.0"
            ]
            payload["revision"] = max(ordinals, default=0) + 1
            tools = self.studio.tools.allowed(self.policy)
            validated = validate_ir(payload, {k: t.signature() for k, t in tools.items()})
            check_supported(validated, tools)
            changed = self.studio.save_ir(
                project_id, project.version, validated.model_dump(), self.policy
            )
            EventStore(self.studio.repository).append(
                project_id,
                None,
                "revision.edited",
                {
                    "upserted_nodes": ids,
                    "deleted_nodes": list(delete_nodes),
                    "outputs_changed": outputs is not None,
                    "inputs_changed": inputs is not None,
                },
                changed.revision_id,
            )
        return changed
