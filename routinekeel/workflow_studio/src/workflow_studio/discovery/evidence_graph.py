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

    def list(self, project_id, include_archived=False):
        ids = self.repository.connection.execute(
            "SELECT DISTINCT graph_id FROM studio_evidence_graphs WHERE project_id=?", (project_id,)
        ).fetchall()
        results = [self.get(project_id, row[0]) for row in ids]
        return [g for g in results if include_archived or g["status"] == "active"]

    def revise_relation(
        self, project_id, graph_id, expected_version, relation_id, *, decision, actor
    ):
        if decision not in {"confirm", "remove"} or not isinstance(actor, str) or not actor.strip():
            raise StudioError("INPUT_INVALID", "關係決策須為確認／移除，並記錄決策者。")
        graph = self.get(project_id, graph_id)["graph"]
        edge = next(
            (edge for edge in graph["relations"] if edge["relation_id"] == relation_id), None
        )
        if edge is None:
            raise StudioError("EVIDENCE_INSUFFICIENT", "關係不存在。")
        if decision == "remove":
            graph["relations"] = [
                item for item in graph["relations"] if item["relation_id"] != relation_id
            ]
        else:
            edge.update(
                confidence_label="human_confirmed", verified_by={"actor": actor, "at": now()}
            )
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
        self, project_id, graph_id, expected_version, relation_id, alternative_id, *, actor
    ):
        if not isinstance(actor, str) or not actor.strip():
            raise StudioError("INPUT_INVALID", "替換關係須記錄決策者。")
        graph = self.get(project_id, graph_id)["graph"]
        relations = {r["relation_id"]: r for r in graph["relations"]}
        current, alternative = relations.get(relation_id), relations.get(alternative_id)
        if (
            current is None
            or alternative is None
            or (current["from"], current["to"]) != (alternative["from"], alternative["to"])
        ):
            raise StudioError("INPUT_INVALID", "替代關係須為相同輸入與輸出的已比對候選。")
        replacement = {
            **alternative,
            "relation_id": relation_id,
            "confidence_label": "human_confirmed",
            "verified_by": {"actor": actor, "at": now()},
        }
        graph["relations"] = [
            replacement if r["relation_id"] == relation_id else r
            for r in graph["relations"]
            if r["relation_id"] != alternative_id or alternative_id == relation_id
        ]
        return self.save(project_id, expected_version, graph, graph_id=graph_id)
