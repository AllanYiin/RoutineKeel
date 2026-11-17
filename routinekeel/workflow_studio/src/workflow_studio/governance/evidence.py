"""Human decisions are bound to exact evidence, never to a model's confidence score."""

import copy
import hashlib
from uuid import uuid4

from ..contracts import StudioError
from ..ir.models import EvidenceRef
from ..llm.streaming import EventStore
from ..project.models import canonical_json
from ..project.service import now


class EvidenceRecords:
    def __init__(self, repository):
        self.repository = repository

    def basis(self, project_id, evidence):
        from ..ingestion.service import SourceService

        source = SourceService(self.repository).get(
            project_id, evidence["source_id"], version=evidence["source_version"]
        )
        locator = evidence["evidence_locator"]
        files = [
            r
            for r in source.manifest["files"]
            if locator == r["path"] or locator.startswith(r["path"] + ":")
        ]
        if not files:
            raise StudioError("EVIDENCE_INSUFFICIENT", "規則位置必須指向這份來源版本中的檔案。")
        record = max(files, key=lambda r: len(r["path"]))
        SourceService(self.repository).content(
            project_id, source.source_id, record["path"], version=source.version
        )
        return {
            "id": evidence["id"],
            "source_id": source.source_id,
            "source_version": source.version,
            "source_hash": source.content_hash,
            "file_hash": record["sha256"],
            "evidence_locator": locator,
            "claim": evidence["claim"],
        }

    def get(self, project_id, identity):
        row = self.repository.connection.execute(
            "SELECT sequence AS decision_order,* FROM studio_evidence_decisions WHERE project_id=? AND decision_id=?",
            (project_id, identity),
        ).fetchone()
        if row is None:
            raise StudioError("EVIDENCE_INSUFFICIENT", "找不到這個專案的確認紀錄。")
        record = dict(row)
        if hashlib.sha256(record["basis_json"].encode()).hexdigest() != record["basis_hash"]:
            raise StudioError("EVIDENCE_INSUFFICIENT", "確認紀錄的內容指紋不符。")
        return record

    def list(self, project_id):
        return [
            self.get(project_id, row[0])
            for row in self.repository.connection.execute(
                "SELECT decision_id FROM studio_evidence_decisions WHERE project_id=? ORDER BY sequence",
                (project_id,),
            )
        ]

    def confirmed_ids(self, project_id, evidence):
        from pydantic import ValidationError

        result = set()
        for item in evidence if isinstance(evidence, list) else []:
            if not isinstance(item, dict) or item.get("confidence_label") != "human_confirmed":
                continue
            try:
                EvidenceRef.model_validate(item)
            except ValidationError:
                continue
            identity = item.get("verified_by")
            if not isinstance(identity, str):
                continue
            record = self.get(project_id, identity)
            revoked = self.repository.connection.execute(
                "SELECT 1 FROM studio_evidence_decisions WHERE project_id=? AND basis_hash=? AND sequence>? AND action='revoke'",
                (project_id, record["basis_hash"], record["decision_order"]),
            ).fetchone()
            if (
                record["action"] == "confirm"
                and not revoked
                and record["basis_json"] == canonical_json(self.basis(project_id, item))
            ):
                result.add(identity)
        return result

    def record(self, project_id, evidence, *, actor, reason, target_id=None):
        if (
            not isinstance(actor, str)
            or not 1 <= len(actor.strip()) <= 200
            or not isinstance(reason, str)
            or not 1 <= len(reason.strip()) <= 4000
        ):
            raise StudioError("INPUT_INVALID", "確認者需 1–200 字，具體理由需 1–4,000 字。")
        basis = self.basis(project_id, evidence)
        if target_id is not None:
            target = self.get(project_id, target_id)
            if target["action"] != "confirm" or target["basis_json"] != canonical_json(basis):
                raise StudioError("EVIDENCE_INSUFFICIENT", "撤回目標與規則內容不符。")
        payload = canonical_json(basis)
        identity = "evidence_decision_" + uuid4().hex
        self.repository.connection.execute(
            "INSERT INTO studio_evidence_decisions (decision_id,project_id,evidence_id,basis_hash,basis_json,action,target_id,actor,reason,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                identity,
                project_id,
                evidence["id"],
                hashlib.sha256(payload.encode()).hexdigest(),
                payload,
                "revoke" if target_id else "confirm",
                target_id,
                actor.strip(),
                reason.strip(),
                now(),
            ),
        )
        return identity


class EvidenceRules:
    def __init__(self, studio, policy):
        self.studio, self.policy = studio, policy
        self.records = EvidenceRecords(studio.repository)

    def _payload(self, project_id, expected_version):
        self.studio.projects._editable(
            self.studio.repository.connection, project_id, expected_version
        )
        return copy.deepcopy(self.studio.editor(self.policy).current(project_id))

    def _commit(self, project_id, expected_version, payload, event, details):
        ordinals = [
            r.ir["revision"]
            for r in self.studio.projects.revisions(project_id, include_discarded=True)
            if r.status != "draft" and r.ir.get("ir_version") == "1.0"
        ]
        payload["revision"] = max(ordinals, default=0) + 1
        revision = self.studio.save_ir(project_id, expected_version, payload, self.policy)
        EventStore(self.studio.repository).append(
            project_id, None, event, details, revision.revision_id
        )
        return revision

    def edit(self, project_id, expected_version, evidence, *, node_ids=None):
        from pydantic import ValidationError

        try:
            item = EvidenceRef.model_validate(evidence).model_dump()
        except ValidationError as exc:
            raise StudioError("INPUT_INVALID", "規則說明、來源與位置不完整。") from exc
        if (
            item["confidence_label"] not in {"observed", "inferred", "conflicted"}
            or item["verified_by"] is not None
        ):
            raise StudioError("APPROVAL_REQUIRED", "確認狀態必須由獨立的人工決策操作建立。")
        with self.studio.repository.transaction():
            payload = self._payload(project_id, expected_version)
            self.records.basis(project_id, item)
            found = next(
                (i for i, e in enumerate(payload["evidence"]) if e["id"] == item["id"]), None
            )
            if found is None:
                payload["evidence"].append(item)
            else:
                payload["evidence"][found] = item
            if node_ids is not None:
                if not set(node_ids) <= {n["id"] for n in payload["nodes"]}:
                    raise StudioError("INPUT_INVALID", "規則綁定了不存在的步驟。")
                for node in payload["nodes"]:
                    node["evidence_refs"] = [r for r in node["evidence_refs"] if r != item["id"]]
                    if node["id"] in node_ids:
                        node["evidence_refs"].append(item["id"])
            return self._commit(
                project_id,
                expected_version,
                payload,
                "evidence.edited",
                {"evidence_id": item["id"]},
            )

    def decide(self, project_id, expected_version, evidence_id, *, actor, reason, revoke=False):
        with self.studio.repository.transaction():
            payload = self._payload(project_id, expected_version)
            item = next((e for e in payload["evidence"] if e["id"] == evidence_id), None)
            if item is None:
                raise StudioError("EVIDENCE_INSUFFICIENT", "找不到要確認的規則。")
            if revoke and item["confidence_label"] != "human_confirmed":
                raise StudioError("INPUT_INVALID", "這項規則沒有可撤回的人工確認。")
            if not revoke and item["confidence_label"] == "human_confirmed":
                raise StudioError("INPUT_INVALID", "此規則已確認；如需重新判斷，請先撤回確認。")
            identity = self.records.record(
                project_id,
                item,
                actor=actor,
                reason=reason,
                target_id=item["verified_by"] if revoke else None,
            )
            item["confidence_label"] = "inferred" if revoke else "human_confirmed"
            item["verified_by"] = None if revoke else identity
            return self._commit(
                project_id,
                expected_version,
                payload,
                "evidence.revoked" if revoke else "evidence.confirmed",
                {"evidence_id": evidence_id, "decision_id": identity},
            )

    def remove(self, project_id, expected_version, evidence_id):
        with self.studio.repository.transaction():
            payload = self._payload(project_id, expected_version)
            if any(evidence_id in n["evidence_refs"] for n in payload["nodes"]):
                raise StudioError(
                    "EVIDENCE_INSUFFICIENT", "仍有步驟引用這項規則，請先調整規則綁定。"
                )
            if not any(e["id"] == evidence_id for e in payload["evidence"]):
                raise StudioError("EVIDENCE_INSUFFICIENT", "找不到這項規則。")
            payload["evidence"] = [e for e in payload["evidence"] if e["id"] != evidence_id]
            return self._commit(
                project_id,
                expected_version,
                payload,
                "evidence.removed",
                {"evidence_id": evidence_id},
            )
