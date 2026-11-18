"""Immutable evidence snapshots with project-level concurrency control."""

import json
from uuid import uuid4

from ..contracts import StudioError
from ..project.models import canonical_json
from ..project.service import ProjectService, now


class EvidenceGraphs:
    def __init__(self, repository):
        self.repository = repository
        self.projects = ProjectService(repository)

    def save(self, project_id, expected_version, payload, *, graph_id=None):
        with self.repository.transaction() as db:
            self.projects._editable(db, project_id, expected_version)
            version = 1
            if graph_id:
                previous = self.get(project_id, graph_id)
                if previous["status"] != "active":
                    raise StudioError("EVIDENCE_INSUFFICIENT", "證據圖已封存，不能修改或重新採用。")
                version = previous["version"] + 1
            else:
                graph_id = "graph_" + uuid4().hex
            db.execute(
                "INSERT INTO studio_evidence_graphs VALUES (?,?,?,?,?,?)",
                (graph_id, version, project_id, canonical_json(payload), "active", now()),
            )
            db.execute(
                "UPDATE studio_projects SET version=version+1,updated_at=? WHERE project_id=?",
                (now(), project_id),
            )
        return self.get(project_id, graph_id)

    def get(self, project_id, graph_id, version=None):
        row = self.repository.connection.execute(
            "SELECT * FROM studio_evidence_graphs WHERE project_id=? AND graph_id=? AND (? IS NULL OR version=?) ORDER BY version DESC LIMIT 1",
            (project_id, graph_id, version, version),
        ).fetchone()
        if row is None:
            raise StudioError("EVIDENCE_INSUFFICIENT", "找不到證據圖版本。")
        result = dict(row)
        result["graph"] = json.loads(result.pop("payload_json"))
        return result

    def active(self, project_id, graph_id):
        from ..ingestion.service import SourceService

        snapshot = self.get(project_id, graph_id)
        if snapshot["status"] != "active":
            raise StudioError("EVIDENCE_INSUFFICIENT", "證據圖已封存。")
        graph = snapshot["graph"]
        source = SourceService(self.repository).get(project_id, graph["source_id"])
        if (
            source.status != "active"
            or source.version != graph["source_version"]
            or source.content_hash != graph["source_hash"]
        ):
            raise StudioError("REVISION_CONFLICT", "來源已改變，請重新比對；原證據圖保留供查閱。")
        return snapshot

    def history(self, project_id, graph_id):
        self.get(project_id, graph_id)
        return [
            self.get(project_id, graph_id, row[0])
            for row in self.repository.connection.execute(
                "SELECT version FROM studio_evidence_graphs WHERE project_id=? AND graph_id=? ORDER BY version",
                (project_id, graph_id),
            ).fetchall()
        ]

    @staticmethod
    def refresh_unknowns(graph):
        reached = {edge["to"] for edge in graph["relations"]}
        graph["unknowns"] = [
            {"path": record["path"], "reason": "缺少足以重建此步驟的輸入或工具證據。"}
            for record in graph["files"]
            if record["role"] == "unknown"
            or (record["role"] in {"output", "intermediate"} and record["path"] not in reached)
        ]

    @staticmethod
    def audit(graph, action, actor, reason, **details):
        if not isinstance(actor, str) or not 1 <= len(actor.strip()) <= 200:
            raise StudioError("INPUT_INVALID", "決策者需 1–200 字。")
        if reason is not None and (
            not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 4000
        ):
            raise StudioError("INPUT_INVALID", "決策理由需 1–4,000 字。")
        record = {
            "action": action,
            "actor": actor.strip(),
            "reason": reason.strip() if reason else None,
            "at": now(),
            **details,
        }
        graph.setdefault("decisions", []).append(record)
        return record

    def list(self, project_id, include_archived=False):
        ids = self.repository.connection.execute(
            "SELECT graph_id FROM studio_evidence_graphs WHERE project_id=? GROUP BY graph_id ORDER BY MIN(created_at),graph_id",
            (project_id,),
        ).fetchall()
        results = [self.get(project_id, row[0]) for row in ids]
        return [g for g in results if include_archived or g["status"] == "active"]

    def revise_relation(
        self, project_id, graph_id, expected_version, relation_id, *, decision, actor, reason=None
    ):
        if (
            decision not in {"confirm", "remove", "unconfirm"}
            or not isinstance(actor, str)
            or not actor.strip()
        ):
            raise StudioError("INPUT_INVALID", "關係決策須為確認／移除，並記錄決策者。")
        graph = self.active(project_id, graph_id)["graph"]
        edge = next(
            (edge for edge in graph["relations"] if edge["relation_id"] == relation_id), None
        )
        if edge is None:
            raise StudioError("EVIDENCE_INSUFFICIENT", "關係不存在。")
        record = self.audit(
            graph, decision, actor, reason, relation_id=relation_id, before=dict(edge)
        )
        if decision == "remove":
            graph["relations"] = [
                item for item in graph["relations"] if item["relation_id"] != relation_id
            ]
        elif decision == "confirm":
            edge.update(
                confidence_label="human_confirmed",
                verified_by={key: record[key] for key in ("actor", "reason", "at")},
            )
        else:
            edge.update(confidence_label="inferred")
            edge.pop("verified_by", None)
        self.refresh_unknowns(graph)
        return self.save(project_id, expected_version, graph, graph_id=graph_id)

    def archive(self, project_id, graph_id, expected_version):
        with self.repository.transaction() as db:
            self.projects._editable(db, project_id, expected_version)
            graph = self.get(project_id, graph_id)
            db.execute(
                "INSERT INTO studio_evidence_graphs VALUES (?,?,?,?,?,?)",
                (
                    graph_id,
                    graph["version"] + 1,
                    project_id,
                    canonical_json(graph["graph"]),
                    "archived",
                    now(),
                ),
            )
            db.execute(
                "UPDATE studio_projects SET version=version+1,updated_at=? WHERE project_id=?",
                (now(), project_id),
            )
        return self.get(project_id, graph_id)

    def replace_relation(
        self,
        project_id,
        graph_id,
        expected_version,
        relation_id,
        alternative_id,
        *,
        actor,
        reason=None,
    ):
        if not isinstance(actor, str) or not actor.strip():
            raise StudioError("INPUT_INVALID", "替換關係須記錄決策者。")
        graph = self.active(project_id, graph_id)["graph"]
        relations = {r["relation_id"]: r for r in graph["relations"]}
        current, alternative = relations.get(relation_id), relations.get(alternative_id)
        if (
            current is None
            or alternative is None
            or (current["from"], current["to"]) != (alternative["from"], alternative["to"])
        ):
            raise StudioError("INPUT_INVALID", "替代關係須為相同輸入與輸出的已比對候選。")
        record = self.audit(
            graph,
            "replace",
            actor,
            reason,
            relation_id=relation_id,
            before=dict(current),
            alternative=dict(alternative),
        )
        replacement = {
            **alternative,
            "relation_id": relation_id,
            "confidence_label": "human_confirmed",
            "verified_by": {key: record[key] for key in ("actor", "reason", "at")},
        }
        graph["relations"] = [
            replacement if r["relation_id"] == relation_id else r
            for r in graph["relations"]
            if r["relation_id"] != alternative_id or alternative_id == relation_id
        ]
        return self.save(project_id, expected_version, graph, graph_id=graph_id)
