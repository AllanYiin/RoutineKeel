"""Versioned domain assets in the Studio catalog and its existing SQLite transaction."""

import hashlib
import json
from dataclasses import dataclass
from uuid import uuid4

from packaging.specifiers import SpecifierSet
from packaging.version import Version
from routinekeel_contracts.asset_loading import load_asset_bytes, load_pack
from routinekeel_contracts.domain_assets import content_hash

from ..contracts import StudioError
from ..project.service import now

_TRANSITIONS = {
    "approve": {"candidate", "disabled"},
    "disable": {"active", "deprecated"},
    "deprecate": {"active"},
    "archive": {"candidate", "disabled", "deprecated"},
    "revoke": {"active", "disabled", "deprecated"},
}


@dataclass(frozen=True)
class CatalogDecision:
    """Evidence delivered by the trusted local interaction adapter."""

    action: str
    ref: str
    sha256: str
    revision: int
    actor: str
    reason: str
    channel: str
    evidence_hash: str | None = None

    @property
    def fingerprint(self):
        return content_hash(vars(self))


class PackRegistry:
    def __init__(self, catalog):
        self.catalog = catalog
        self.repository = catalog.repository

    def register(self, path):
        pack = load_pack(path)
        manifest = pack.manifest.model
        ref = manifest.pack.ref
        with self.repository.transaction() as db:
            existing = db.execute(
                "SELECT manifest_hash FROM studio_domain_packs WHERE ref=?", (ref,)
            ).fetchone()
            if existing:
                if existing[0] != pack.manifest.sha256:
                    raise StudioError("REVISION_CONFLICT", "此 Pack 版本已存在，請使用新版本。")
                self.get(ref)
            else:
                db.execute(
                    "INSERT INTO studio_domain_packs VALUES (?,?,?,?,?,'candidate',0,1,?)",
                    (
                        ref,
                        pack.manifest.canonical,
                        pack.manifest.sha256,
                        pack.source_sha256,
                        pack.archive,
                        now(),
                    ),
                )
                db.executemany(
                    "INSERT INTO studio_pack_dependencies VALUES (?,?)",
                    [(ref, dependency) for dependency in manifest.dependencies],
                )
                for asset in pack.definitions:
                    self._register_asset(db, ref, asset)
                self._event(db, "pack", ref, "register", pack.manifest.sha256, 1)
        return self.get(ref)

    def _register_asset(self, db, pack_ref, loaded):
        asset = loaded.model
        ref = asset.pattern.ref if loaded.kind == "pattern" else asset.ref
        existing = db.execute(
            "SELECT content_hash FROM studio_domain_assets WHERE kind=? AND ref=?",
            (loaded.kind, ref),
        ).fetchone()
        if existing and existing[0] != loaded.sha256:
            raise StudioError("REVISION_CONFLICT", "資產版本已存在，內容變更須用新版本。")
        if not existing:
            db.execute(
                "INSERT INTO studio_domain_assets VALUES (?,?,?,?)",
                (loaded.kind, ref, loaded.canonical, loaded.sha256),
            )
            if loaded.kind == "pattern":
                db.execute(
                    "INSERT INTO studio_pattern_records VALUES (?,'candidate',1,NULL)", (ref,)
                )
                db.executemany(
                    "INSERT INTO studio_pattern_requirements VALUES (?,?,?,?,?)",
                    [
                        (ref, rule.id, rule.capability, rule.version, rule.required)
                        for rule in asset.requirements
                    ],
                )
            self._event(db, loaded.kind, ref, "register", loaded.sha256, 1)
        db.execute(
            "INSERT INTO studio_pack_members VALUES (?,?,?,?)",
            (pack_ref, loaded.kind, ref, loaded.source_sha256),
        )

    def _event(self, db, kind, ref, action, digest, revision, decision=None):
        db.execute(
            """INSERT INTO studio_domain_events
            (event_id,kind,ref,action,content_hash,revision,actor,reason,review_channel,
             decision_hash,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                uuid4().hex,
                kind,
                ref,
                action,
                digest,
                revision,
                decision.actor if decision else None,
                decision.reason if decision else None,
                decision.channel if decision else None,
                decision.fingerprint if decision else None,
                now(),
            ),
        )

    def get(self, ref):
        row = self.repository.connection.execute(
            """SELECT ref,manifest,manifest_hash,source_hash,status,approved,revision,created_at
            FROM studio_domain_packs WHERE ref=?""",
            (ref,),
        ).fetchone()
        if row is None:
            raise StudioError("DOMAIN_ASSET_UNKNOWN", "未登錄指定的 Pack 版本。")
        item = dict(row)
        manifest = load_asset_bytes("pack", item.pop("manifest"))
        if manifest.sha256 != item["manifest_hash"] or manifest.model.pack.ref != ref:
            raise StudioError("CONTRACT_MISMATCH", "Pack manifest 指紋或版本不符。")
        item["manifest"] = manifest.model.model_dump(mode="json")
        item["trust_status"] = "approved" if item.pop("approved") else "pending_approval"
        return item

    def list(self, *, include_archived=False):
        rows = self.repository.connection.execute(
            "SELECT ref FROM studio_domain_packs WHERE status<>'archived' OR ? ORDER BY ref",
            (include_archived,),
        )
        return [self.get(row[0]) for row in rows]

    def get_asset(self, kind, ref):
        row = self.repository.connection.execute(
            "SELECT body,content_hash FROM studio_domain_assets WHERE kind=? AND ref=?", (kind, ref)
        ).fetchone()
        if row is None:
            raise StudioError("DOMAIN_ASSET_UNKNOWN", "未登錄指定的資產版本。")
        asset = load_asset_bytes(kind, row["body"])
        if asset.sha256 != row["content_hash"]:
            raise StudioError("CONTRACT_MISMATCH", "可攜資產指紋不符。")
        return asset

    def archive_bytes(self, ref):
        item = self.get(ref)
        row = self.repository.connection.execute(
            "SELECT archive FROM studio_domain_packs WHERE ref=?", (ref,)
        ).fetchone()
        if hashlib.sha256(row[0]).hexdigest() != item["source_hash"]:
            raise StudioError("CONTRACT_MISMATCH", "Pack 原始來源指紋不符。")
        return row[0]

    def _lineage(self, ref, runtime_version=None, seen=frozenset()):
        if ref in seen:
            raise StudioError("PATTERN_DEPENDENCY_CONFLICT", "Pack 依賴形成循環。")
        item = self.get(ref)
        runtime_range = item["manifest"]["pack"]["runtime_compatibility"]
        if runtime_version is not None and not SpecifierSet(runtime_range).contains(
            runtime_version
        ):
            raise StudioError("CONTRACT_MISMATCH", "目標底座版本不在 Pack 相容範圍。")
        for dependency in item["manifest"]["dependencies"]:
            target = self.get(dependency)
            if target["status"] != "active" or target["trust_status"] != "approved":
                raise StudioError("PATTERN_DEPENDENCY_CONFLICT", "Pack 有停用或未核准的依賴。")
            self._lineage(dependency, runtime_version, seen | {ref})

    def require_available(self, ref, runtime_version):
        item = self.get(ref)
        if item["status"] != "active" or item["trust_status"] != "approved":
            raise StudioError("PATTERN_APPROVAL_REQUIRED", "新建置需要有效且已核准的 Pack。")
        self._lineage(ref, runtime_version)
        return item

    def _check_decision(self, item, decision, *, digest):
        if not isinstance(decision, CatalogDecision) or decision.channel not in {
            "local_terminal",
            "desktop_user_reply",
        }:
            raise StudioError("PATTERN_APPROVAL_REQUIRED", "請由本機可信互動入口確認這個版本。")
        self.catalog._reviewer(decision.actor, decision.reason, required=True)
        if (decision.ref, decision.sha256, decision.revision) != (
            item["ref"],
            digest,
            item["revision"],
        ):
            raise StudioError("REVISION_CONFLICT", "資產狀態或內容已變動，請重新核對。")

    def decide(self, decision):
        if not isinstance(decision, CatalogDecision):
            raise StudioError("PATTERN_APPROVAL_REQUIRED", "缺少可信的本機決策。")
        with self.repository.transaction() as db:
            item = self.get(decision.ref)
            self._check_decision(item, decision, digest=item["manifest_hash"])
            if item["status"] not in _TRANSITIONS.get(decision.action, ()):
                raise StudioError("INPUT_INVALID", "Pack 目前狀態不支援此決策。")
            approved = item["trust_status"] == "approved"
            status = {
                "approve": "active",
                "disable": "disabled",
                "deprecate": "deprecated",
                "archive": "archived",
                "revoke": "disabled",
            }[decision.action]
            if decision.action == "approve":
                self._lineage(decision.ref)
                approved = True
            elif decision.action == "revoke":
                approved = False
            revision = item["revision"] + 1
            updated = db.execute(
                """UPDATE studio_domain_packs SET status=?,approved=?,revision=?
                WHERE ref=? AND revision=?""",
                (status, approved, revision, decision.ref, decision.revision),
            )
            if updated.rowcount != 1:
                raise StudioError("REVISION_CONFLICT", "Pack 已經由其他程序更新。")
            self._event(
                db, "pack", decision.ref, decision.action, item["manifest_hash"], revision, decision
            )
        return self.get(decision.ref)

    def history(self, ref, *, kind="pack"):
        if kind == "pack":
            self.get(ref)
        else:
            self.get_asset(kind, ref)
        return [
            dict(row)
            for row in self.repository.connection.execute(
                "SELECT * FROM studio_domain_events WHERE kind=? AND ref=? ORDER BY sequence",
                (kind, ref),
            )
        ]

    def pattern_pack_refs(self, pattern_ref):
        return [
            row[0]
            for row in self.repository.connection.execute(
                """SELECT pack_ref FROM studio_pack_members
                WHERE kind='pattern' AND asset_ref=? ORDER BY pack_ref""",
                (pattern_ref,),
            )
        ]

    def pattern_status(self, pattern_ref):
        asset = self.get_asset("pattern", pattern_ref)
        row = self.repository.connection.execute(
            "SELECT * FROM studio_pattern_records WHERE ref=?", (pattern_ref,)
        ).fetchone()
        if row is None:
            raise StudioError("CONTRACT_MISMATCH", "Pattern 缺少生命週期紀錄。")
        result = dict(row)
        result["content_hash"] = asset.sha256
        result["trust_status"] = "approved" if row["qualification_hash"] else "pending_approval"
        result["pack_refs"] = self.pattern_pack_refs(pattern_ref)
        return result

    def patterns(self, *, include_inactive=False, domain=None):
        rows = self.repository.connection.execute(
            "SELECT ref FROM studio_pattern_records WHERE status='active' OR ? ORDER BY ref",
            (include_inactive,),
        )
        result = []
        for row in rows:
            item = self.pattern_status(row[0])
            if not include_inactive and not self._has_active_pack(row[0]):
                continue
            manifest = self.get_asset("pattern", row[0]).model
            if domain is None or domain in manifest.pattern.declared_domains:
                result.append(item)
        return result

    def _has_active_pack(self, pattern_ref):
        row = self.repository.connection.execute(
            """SELECT p.qualification_hash,a.content_hash FROM studio_pattern_records p
            JOIN studio_domain_assets a ON a.kind='pattern' AND a.ref=p.ref
            WHERE p.ref=?""",
            (pattern_ref,),
        ).fetchone()
        if row is None or not row[0]:
            return False
        report = self._read_trial(pattern_ref, row[1], row[0])
        try:
            pack = self.get(report["pack_ref"])
            self._lineage(pack["ref"])
        except StudioError:
            return False
        return pack["status"] == "active" and pack["manifest_hash"] == report["pack_hash"]

    def pattern_metadata(self):
        rows = self.repository.connection.execute(
            """SELECT ref FROM studio_pattern_records WHERE status='active' ORDER BY ref"""
        )
        return [self.inspect_pattern(row[0], "L1") for row in rows if self._has_active_pack(row[0])]

    def verify_composition(self, composition):
        from routinekeel_contracts.pattern_composition import PatternComposition

        requested = PatternComposition.model_validate(composition)
        item = self.pattern_status(requested.ref)
        if item["content_hash"] != requested.hash:
            raise StudioError("CONTRACT_MISMATCH", "Pattern 內容與藍圖不同。")
        if (
            item["status"] != "active"
            or item["qualification_hash"] != requested.qualification_hash
            or not self._has_active_pack(requested.ref)
        ):
            raise StudioError(
                "PATTERN_APPROVAL_REQUIRED", "Pattern 或 Pack 已停用，請重新確認藍圖。"
            )
        trial = self.trial(requested.ref, requested.qualification_hash)
        if (trial["pack_ref"], trial["pack_hash"]) != (requested.pack_ref, requested.pack_hash):
            raise StudioError("CONTRACT_MISMATCH", "Pattern 資格與藍圖宣告的 Pack 不符。")

    def inspect_pattern(self, pattern_ref, level):
        properties = {
            "L1": {
                "title": "$.pattern.title",
                "domains": "$.pattern.declared_domains",
                "task_categories": "$.triggers.design.task_categories",
                "priority": "$.triggers.design.priority",
                "gate": "$.triggers.design.gate",
            },
            "L2": {
                "title": "$.pattern.title",
                "boundary": "$.boundary",
                "requirements": "$.requirements",
                "design": "$.triggers.design",
                "policy_refs": "$.policy_refs",
                "profile_schema_refs": "$.profile_schema_refs",
                "profile_parameters": "$.profile_parameters",
                "quality_contract": "$.quality_contract",
            },
        }
        if level == "L3":
            asset = self.get_asset("pattern", pattern_ref)
            return {
                "ref": pattern_ref,
                "hash": asset.sha256,
                "level": level,
                "pattern": asset.model.model_dump(mode="json"),
            }
        if level not in properties:
            raise StudioError("INPUT_INVALID", "Pattern 查閱層級應為 L1、L2 或 L3。")
        paths = properties[level]
        columns = ", ".join(
            f"json_quote(json_extract(CAST(body AS TEXT), '{path}')) AS {name}"
            for name, path in paths.items()
        )
        row = self.repository.connection.execute(
            f"""SELECT content_hash,{columns} FROM studio_domain_assets
            WHERE kind='pattern' AND ref=?""",
            (pattern_ref,),
        ).fetchone()
        if not row:
            raise StudioError("DOMAIN_ASSET_UNKNOWN", "未登錄指定的 Pattern 版本。")
        payload = {name: json.loads(row[name]) for name in paths}
        return {"ref": pattern_ref, "hash": row["content_hash"], "level": level, **payload}

    def decide_pattern(self, decision):
        if not isinstance(decision, CatalogDecision):
            raise StudioError("PATTERN_APPROVAL_REQUIRED", "缺少可信的本機決策。")
        with self.repository.transaction() as db:
            item = self.pattern_status(decision.ref)
            self._check_decision(item, decision, digest=item["content_hash"])
            if item["status"] not in _TRANSITIONS.get(decision.action, ()):
                raise StudioError("INPUT_INVALID", "Pattern 目前狀態不支援此決策。")
            status = {
                "approve": "active",
                "disable": "disabled",
                "deprecate": "deprecated",
                "archive": "archived",
                "revoke": "disabled",
            }[decision.action]
            qualification = item["qualification_hash"]
            if decision.action == "approve":
                qualification_hash = decision.evidence_hash
                from ..patterns.evolution import require_regression

                require_regression(db, decision.ref, item["content_hash"], qualification_hash)
                trial = db.execute(
                    """SELECT report FROM studio_pattern_trials WHERE pattern_ref=?
                    AND pattern_hash=? AND report_hash=? AND status='PASS'""",
                    (decision.ref, item["content_hash"], qualification_hash),
                ).fetchone()
                report = json.loads(trial[0]) if trial else {}
                if (
                    not report
                    or content_hash(report) != qualification_hash
                    or report.get("pattern_ref") != decision.ref
                    or report.get("pattern_hash") != item["content_hash"]
                    or report.get("pack_ref") not in item["pack_refs"]
                    or report.get("verdict") != "PASS"
                    or report.get("quality_report", {}).get("verdict") != "PASS"
                    or not report.get("conformance_report", {}).get("builder_receipt")
                ):
                    raise StudioError(
                        "PATTERN_APPROVAL_REQUIRED", "需要有效的 Pack 與實際試做收據。"
                    )
                pack = self.get(report["pack_ref"])
                if pack["status"] != "active" or pack["manifest_hash"] != report.get("pack_hash"):
                    raise StudioError("PATTERN_APPROVAL_REQUIRED", "試做所用的 Pack 已變更或停用。")
                self._lineage(pack["ref"])
                qualification = qualification_hash
            elif decision.action == "revoke":
                qualification = None
            revision = item["revision"] + 1
            updated = db.execute(
                """UPDATE studio_pattern_records SET status=?,revision=?,qualification_hash=?
                WHERE ref=? AND revision=?""",
                (status, revision, qualification, decision.ref, decision.revision),
            )
            if updated.rowcount != 1:
                raise StudioError("REVISION_CONFLICT", "Pattern 已經由其他程序更新。")
            self._event(
                db,
                "pattern",
                decision.ref,
                decision.action,
                item["content_hash"],
                revision,
                decision,
            )
        return self.pattern_status(decision.ref)

    def trial(self, pattern_ref, qualification_hash):
        item = self.get_asset("pattern", pattern_ref)
        return self._read_trial(pattern_ref, item.sha256, qualification_hash)

    def _read_trial(self, pattern_ref, pattern_hash, qualification_hash):
        row = self.repository.connection.execute(
            """SELECT report FROM studio_pattern_trials
            WHERE pattern_ref=? AND pattern_hash=? AND report_hash=?""",
            (pattern_ref, pattern_hash, qualification_hash),
        ).fetchone()
        if row is None:
            raise StudioError("DOMAIN_ASSET_UNKNOWN", "未登錄指定的 Pattern 試做收據。")
        report = json.loads(row[0])
        if content_hash(report) != qualification_hash:
            raise StudioError("CONTRACT_MISMATCH", "Pattern 試做收據指紋不符。")
        return {**report, "qualification_hash": qualification_hash}

    def pin_workflow(self, workflow_ref, blueprint_hash, pattern_ref, pack_ref, runtime_version):
        with self.repository.transaction() as db:
            existing = db.execute(
                """SELECT pattern_hash,pack_ref FROM studio_pattern_pins
                WHERE workflow_ref=? AND blueprint_hash=? AND pattern_ref=?""",
                (workflow_ref, blueprint_hash, pattern_ref),
            ).fetchone()
            asset = self.get_asset("pattern", pattern_ref)
            if existing:
                if tuple(existing) != (asset.sha256, pack_ref):
                    raise StudioError("REVISION_CONFLICT", "工作流已鎖定其他資產來源。")
                return asset
            self.require_available(pack_ref, runtime_version)
            item = self.pattern_status(pattern_ref)
            if item["status"] != "active" or pack_ref not in item["pack_refs"]:
                raise StudioError("PATTERN_APPROVAL_REQUIRED", "新建置需要有效且已核准的 Pattern。")
            report = self.trial(pattern_ref, item["qualification_hash"])
            if report["pack_ref"] != pack_ref:
                raise StudioError("PATTERN_APPROVAL_REQUIRED", "新建置須使用實際通過試做的 Pack。")
            db.execute(
                "INSERT INTO studio_pattern_pins VALUES (?,?,?,?,?,?)",
                (workflow_ref, blueprint_hash, pattern_ref, asset.sha256, pack_ref, now()),
            )
        return asset

    def check_update(self, pattern_ref):
        asset = self.get_asset("pattern", pattern_ref)
        asset_id, version = asset.model.pattern.id, asset.model.pattern.version
        return sorted(
            [
                item["ref"]
                for item in self.patterns()
                if item["ref"].split("@")[0] == asset_id
                and Version(item["ref"].split("@")[1]) > Version(version)
            ],
            key=lambda ref: Version(ref.split("@")[1]),
        )

    def dependents(self, pattern_ref):
        self.get_asset("pattern", pattern_ref)
        return [
            dict(row)
            for row in self.repository.connection.execute(
                """SELECT workflow_ref,blueprint_hash,pattern_hash,pack_ref
                FROM studio_pattern_pins WHERE pattern_ref=? ORDER BY workflow_ref,blueprint_hash""",
                (pattern_ref,),
            )
        ]

    def by_capability(self, capability_id):
        return [
            dict(row)
            for row in self.repository.connection.execute(
                """SELECT pattern_ref,requirement_id,capability_version,required
                FROM studio_pattern_requirements WHERE capability_id=?
                ORDER BY pattern_ref,requirement_id""",
                (capability_id,),
            )
        ]

    def dependent_packs(self, pack_ref):
        return [
            row[0]
            for row in self.repository.connection.execute(
                """SELECT pack_ref FROM studio_pack_dependencies
                WHERE dependency_ref=? ORDER BY pack_ref""",
                (pack_ref,),
            )
        ]
