"""Local folder evidence discovery and bounded, trustworthy sample comparisons."""

from pathlib import PurePosixPath

from ..contracts import StudioError
from .evidence_graph import EvidenceGraphs
from .provenance import replay_candidates


def infer_role(path):
    parts = {p.lower() for p in PurePosixPath(path).parts[:-1]}
    if parts & {"input", "inputs", "raw", "original", "原始", "輸入"}:
        return "input"
    if parts & {"output", "outputs", "expected", "final", "成果", "輸出"}:
        return "output"
    if parts & {"intermediate", "temporary", "中間", "暫存"}:
        return "intermediate"
    if PurePosixPath(path).suffix.lower() == ".log":
        return "log"
    return "unknown"


class FolderDiscovery:
    def __init__(self, studio):
        self.studio = studio

    def analyze(self, project_id, source_id, expected_version, *, roles=None, max_comparisons=200):
        if type(max_comparisons) is not int or not 1 <= max_comparisons <= 1000:
            raise StudioError("INPUT_INVALID", "樣本比對上限須介於 1 與 1000。")
        source = self.studio.sources.get(project_id, source_id)
        if source.status != "active":
            raise StudioError("SOURCE_REMOVED", "來源已移除。")
        roles = roles or {}
        files = source.manifest["files"]
        if not set(roles) <= {r["path"] for r in files} or not set(roles.values()) <= {
            "input",
            "output",
            "intermediate",
            "log",
            "unknown",
        }:
            raise StudioError("INPUT_INVALID", "檔案角色或路徑無效。")
        graph = {
            "source_id": source_id,
            "source_version": source.version,
            "source_hash": source.content_hash,
            "files": [],
            "relations": [],
            "unknowns": [],
            "comparison_limit": max_comparisons,
            "scope": "local_sample_evidence_not_historical_causation",
        }
        for record in files:
            role = roles.get(record["path"], infer_role(record["path"]))
            graph["files"].append(
                {
                    "path": record["path"],
                    "sha256": record["sha256"],
                    "size": record["size"],
                    "media_type": record["preview"]["media_type"],
                    "observed_mtime_ns": record.get("observed_mtime_ns"),
                    "role": role,
                    "role_basis": "user_supplied"
                    if record["path"] in roles
                    else "filename_inference",
                }
            )
        candidates = [r for r in graph["files"] if r["role"] in {"input", "intermediate"}]
        outputs = [r for r in graph["files"] if r["role"] in {"output", "intermediate"}]
        comparisons = 0
        for target in outputs:
            for origin in candidates:
                if origin["path"] == target["path"]:
                    continue
                if comparisons >= max_comparisons:
                    break
                comparisons += 1
                before = self.studio.sources.content(
                    project_id, source_id, origin["path"], version=source.version
                )
                expected = self.studio.sources.content(
                    project_id, source_id, target["path"], version=source.version
                )
                for match in replay_candidates(before, expected):
                    graph["relations"].append(
                        {
                            "relation_id": f"relation-{len(graph['relations']) + 1}",
                            "from": origin["path"],
                            "to": target["path"],
                            "source_id": source_id,
                            "source_version": source.version,
                            "input_sha256": origin["sha256"],
                            **match,
                        }
                    )
        reached = {e["to"] for e in graph["relations"]}
        for record in graph["files"]:
            if record["role"] == "unknown" or (
                record["role"] in {"output", "intermediate"} and record["path"] not in reached
            ):
                graph["unknowns"].append(
                    {"path": record["path"], "reason": "缺少足以重建此步驟的輸入或工具證據。"}
                )
        graph["comparisons"] = comparisons
        graph["time_observations"] = [
            {
                "from": edge["from"],
                "to": edge["to"],
                "input_before_output": next(r for r in graph["files"] if r["path"] == edge["from"])[
                    "observed_mtime_ns"
                ]
                <= next(r for r in graph["files"] if r["path"] == edge["to"])["observed_mtime_ns"],
                "causality_verified": False,
            }
            for edge in graph["relations"]
            if next(r for r in graph["files"] if r["path"] == edge["from"])["observed_mtime_ns"]
            is not None
            and next(r for r in graph["files"] if r["path"] == edge["to"])["observed_mtime_ns"]
            is not None
        ]
        graph["log_observations"] = []
        for record in graph["files"]:
            if record["role"] == "log":
                content = self.studio.sources.content(
                    project_id, source_id, record["path"], version=source.version
                ).decode("utf-8-sig")
                mentions = [
                    r["path"]
                    for r in graph["files"]
                    if r["path"] != record["path"] and r["path"] in content
                ]
                graph["log_observations"].append(
                    {
                        "path": record["path"],
                        "referenced_files": mentions,
                        "confidence_label": "observed",
                        "claim": "日誌提到這些檔案；未將日誌敘述自動當成已驗證因果。",
                    }
                )
        graph["timestamps"] = "未將檔案時間或檔名順序視為因果證據；來源副本以內容指紋為準。"
        return EvidenceGraphs(self.studio.repository).save(project_id, expected_version, graph)

    def candidate_irs(self, project_id, graph_id, policy, *, max_candidates=20):
        """Enumerate bounded acyclic paths; every result remains an inferred candidate."""
        from ..ir.validator import validate_ir

        if type(max_candidates) is not int or not 1 <= max_candidates <= 20:
            raise StudioError("INPUT_INVALID", "候選數須介於 1 與 20。")
        snapshot = EvidenceGraphs(self.studio.repository).get(project_id, graph_id)
        if snapshot["status"] != "active":
            raise StudioError("EVIDENCE_INSUFFICIENT", "證據圖已封存。")
        graph = snapshot["graph"]
        project = self.studio.projects.get(project_id)
        tools = self.studio.tools.allowed(policy)
        accepted = [
            r.ir
            for r in self.studio.projects.revisions(project_id)
            if r.ir.get("ir_version") == "1.0" and r.status != "draft"
        ]
        ordinal = max((r["revision"] for r in accepted), default=0) + 1
        workflow_id = accepted[-1]["workflow_id"] if accepted else "workflow-" + project_id[-12:]
        outputs = {r["path"] for r in graph["files"] if r["role"] == "output"}
        pending = [(r["path"], [], {r["path"]}) for r in graph["files"] if r["role"] == "input"]
        results, visited = [], 0
        while pending and len(results) < max_candidates and visited < 1000:
            location, path, seen = pending.pop(0)
            visited += 1
            if location not in outputs:
                for edge in graph["relations"]:
                    if edge["from"] == location and edge["to"] not in seen:
                        pending.append((edge["to"], path + [edge], seen | {edge["to"]}))
                continue
            nodes = []
            evidence = []
            for i, edge in enumerate(path):
                tool = tools.get(f"{edge['tool']}@{edge['tool_version']}")
                if tool is None:
                    break
                node_id, evidence_id = f"step{i + 1}", f"evidence{i + 1}"
                port = tool.outputs["result"].model_dump()
                port["path"] = f"artifacts/{node_id}.{port.get('format') or 'dat'}"
                nodes.append(
                    {
                        "id": node_id,
                        "kind": tool.kind,
                        "tool": tool.tool_id,
                        "tool_version": tool.version,
                        "inputs": {
                            "source": {
                                "source": "input://source"
                                if i == 0
                                else f"artifact://step{i}/result",
                                "port": tool.inputs["source"].model_dump(),
                            }
                        },
                        "outputs": {"result": port},
                        "depends_on": [] if i == 0 else [f"step{i}"],
                        "parameters": edge["parameters"],
                        "preconditions": [],
                        "postconditions": [
                            {"operator": "exists", "reference": f"artifact://{node_id}/result"}
                        ],
                        "side_effects": tool.side_effects,
                        "retry_policy": {"mode": "never", "max_attempts": 1},
                        "cache_policy": {
                            "mode": "content_addressed",
                            "fingerprint_fields": ["inputs", "parameters", "tool_version"],
                        },
                        "platform_requirements": tool.platforms,
                        "resources": tool.resources.model_dump(),
                        "timeout_seconds": 60,
                        "evidence_refs": [evidence_id],
                    }
                )
                evidence.append(
                    {
                        "id": evidence_id,
                        "source_id": graph["source_id"],
                        "source_version": graph["source_version"],
                        "evidence_locator": edge["to"],
                        "claim": edge["claim"],
                        "confidence_label": "inferred",
                    }
                )
            if len(nodes) != len(path) or not nodes:
                continue
            payload = {
                "ir_version": "1.0",
                "workflow_id": workflow_id,
                "revision": ordinal,
                "goal": project.goal,
                "inputs": {"source": nodes[0]["inputs"]["source"]["port"]},
                "secrets": [],
                "nodes": nodes,
                "outputs": {"result": f"artifact://step{len(nodes)}/result"},
                "evidence": evidence,
            }
            try:
                validated = validate_ir(
                    payload,
                    {key: t.signature() for key, t in tools.items()},
                    source_versions={(graph["source_id"], graph["source_version"])},
                )
            except StudioError:
                continue
            results.append(
                {
                    "ir": validated.model_dump(),
                    "graph_id": graph_id,
                    "graph_version": snapshot["version"],
                    "relations": [edge["relation_id"] for edge in path],
                    "input_path": path[0]["from"],
                    "expected_path": path[-1]["to"],
                }
            )
        return results
