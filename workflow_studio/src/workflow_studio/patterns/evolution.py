"""Stage and verify versioned Pattern improvements using observed Runtime evidence."""

import hmac
import json
import os

from packaging.version import Version
from routinekeel_contracts.domain_assets import canonical_json, content_hash
from routinekeel_contracts.privacy import contains_private_data

from ..contracts import StudioError
from ..project.service import now


def require_runtime_evidence(params):
    proof = os.environ.get("ROUTINEKEEL_STUDIO_EVIDENCE_SHA256", "")
    if not hmac.compare_digest(proof, content_hash(params)):
        raise StudioError("PATTERN_APPROVAL_REQUIRED", "改善證據須從本機 Runtime 讀取。")


def attribution(rows):
    groups = {}
    output = []
    for row in rows:
        signals = set(row["signs"])
        issue = signals - {"pattern_defect", "security"}
        if row["scope"] == "preference" or "profile_preference" in signals:
            bucket = "profile_preference"
        elif "adapter_difference" in signals:
            bucket = "adapter_difference"
        elif row["scope"] == "workflow" or "configuration" in signals:
            bucket = "workflow_misconfiguration"
        elif "pattern_defect" in signals and (
            row["before_hash"] != row["after_hash"] or row["failed_checks"]
        ):
            bucket = "possible_pattern_defect"
            key = (row["pattern_hash"], tuple(sorted(issue)))
            groups.setdefault(key, set()).add(row["workflow_ref"].split("@")[0])
        else:
            bucket = "unresolved"
        output.append({**row, "attribution": bucket})
    corroborated = any(len(independent) >= 2 for independent in groups.values())
    urgent = any(
        "security" in row["signs"]
        and row["failed_checks"]
        and row["attribution"] == "possible_pattern_defect"
        for row in output
    )
    return {
        "observations": output,
        "corroborated": corroborated,
        "urgent": urgent,
        "uncertainty": "標籤及重複訊號是原因假設，須以原樣本和比較試做驗證。",
    }


def _read(db, proposal_id):
    row = db.execute(
        """SELECT body FROM studio_pattern_improvement_revisions WHERE proposal_id=?
        ORDER BY revision DESC LIMIT 1""",
        (proposal_id,),
    ).fetchone()
    if row is None:
        raise StudioError("DOMAIN_ASSET_UNKNOWN", "找不到此 Pattern 改善提案。")
    return json.loads(row[0])


def get(studio, proposal_id):
    return _read(studio.repository.connection, proposal_id)


def propose(studio, source_ref, target_ref, request_id, evidence):
    registry = studio.tools.packs
    source = registry.get_asset("pattern", source_ref)
    target = registry.get_asset("pattern", target_ref)
    old, new = source.model.pattern, target.model.pattern
    if old.id != new.id or Version(new.version) <= Version(old.version):
        raise StudioError("REVISION_CONFLICT", "Pattern 改善須另建同一識別碼的新版本。")
    if registry.pattern_status(target_ref)["status"] != "candidate":
        raise StudioError("REVISION_CONFLICT", "改善候選須在核准前進入回歸比較。")
    report = attribution(evidence["attribution"])
    if any(row["pattern_hash"] != source.sha256 for row in report["observations"]):
        raise StudioError("CONTRACT_MISMATCH", "品質回饋與來源 Pattern 指紋不一致。")
    if contains_private_data(evidence):
        raise StudioError("SECRET_IN_PORTABLE_PACKAGE", "改善證據含私人資料。")
    if not report["corroborated"] and not report["urgent"]:
        return {"status": "review_only", "attribution": report, "source_ref": source_ref}
    identity = content_hash([source_ref, target_ref, request_id])
    proposal = {
        "proposal_id": identity,
        "source_ref": source_ref,
        "source_hash": source.sha256,
        "target_ref": target_ref,
        "target_hash": target.sha256,
        "dependents": evidence["dependents"],
        "attribution": report,
        "changes": [
            field
            for field in ("boundary", "graph", "requirements", "triggers", "quality_contract")
            if getattr(source.model, field) != getattr(target.model, field)
        ],
        "status": "proposed",
        "revision": 1,
    }
    with studio.repository.transaction() as db:
        existing = db.execute(
            "SELECT id FROM studio_pattern_improvements WHERE target_ref=?", (target_ref,)
        ).fetchone()
        if existing:
            if existing["id"] != identity:
                raise StudioError("REVISION_CONFLICT", "目標版本已有其他改善提案。")
            first = db.execute(
                "SELECT body FROM studio_pattern_improvement_revisions WHERE proposal_id=? AND revision=1",
                (identity,),
            ).fetchone()
            if json.loads(first["body"]) != proposal:
                raise StudioError("REVISION_CONFLICT", "同一請求已有不同的來源或影響範圍。")
            return _read(db, identity)
        db.execute(
            "INSERT INTO studio_pattern_improvements VALUES (?,?,?,?)",
            (identity, source_ref, target_ref, now()),
        )
        db.execute(
            "INSERT INTO studio_pattern_improvement_revisions VALUES (?,?,?)",
            (identity, 1, canonical_json(proposal)),
        )
    return proposal


def record_comparison(studio, proposal_id, expected_revision, source_trial, target_trial, report):
    registry = studio.tools.packs
    with studio.repository.transaction() as db:
        proposal = _read(db, proposal_id)
        if expected_revision != proposal["revision"]:
            raise StudioError("REVISION_CONFLICT", "改善提案已更新，請重新核對。")
        old = registry.trial(proposal["source_ref"], source_trial)
        new = registry.trial(proposal["target_ref"], target_trial)
        required = {row["workflow_ref"] for row in proposal["dependents"]}
        checked = {row["workflow_ref"] for row in report["comparisons"]}
        passed = (
            old["verdict"] == new["verdict"] == report["status"] == "PASS"
            and required == checked
            and all(item["passed"] for item in report["comparisons"])
        )
        if contains_private_data(report):
            raise StudioError("SECRET_IN_PORTABLE_PACKAGE", "比較結果含私人資料。")
        proposal.update(
            revision=expected_revision + 1,
            status="eligible" if passed else "regression_failed",
            comparison=report,
            comparison_hash=content_hash(report),
            source_pattern_trial=source_trial,
            target_pattern_trial=target_trial,
        )
        db.execute(
            "INSERT INTO studio_pattern_improvement_revisions VALUES (?,?,?)",
            (proposal_id, proposal["revision"], canonical_json(proposal)),
        )
    return proposal


def require_regression(db, pattern_ref, sha256, qualification_hash):
    row = db.execute(
        "SELECT id FROM studio_pattern_improvements WHERE target_ref=?", (pattern_ref,)
    ).fetchone()
    if row is not None:
        proposal = _read(db, row["id"])
        if (
            proposal["status"] != "eligible"
            or proposal["target_hash"] != sha256
            or proposal["target_pattern_trial"] != qualification_hash
        ):
            raise StudioError(
                "PATTERN_REGRESSION_FAILED", "改版須先通過所有受影響工作流的回歸驗證。"
            )


def rehearsal_trial(studio, proposal_id, pattern_ref, pattern_hash, trial_hash):
    proposal = get(studio, proposal_id)
    if (proposal["target_ref"], proposal["target_hash"]) != (pattern_ref, pattern_hash):
        raise StudioError("PATTERN_REGRESSION_FAILED", "回歸草稿與改善提案的目標版本不同。")
    registry = studio.tools.packs
    trial = registry.trial(pattern_ref, trial_hash)
    pack = registry.get(trial["pack_ref"])
    if (
        trial["verdict"] != "PASS"
        or pack["status"] != "active"
        or pack["manifest_hash"] != trial["pack_hash"]
    ):
        raise StudioError(
            "PATTERN_APPROVAL_REQUIRED", "回歸試做需要目標 Pattern 與 Pack 的有效資格。"
        )
    registry._lineage(pack["ref"])
    return proposal, pack
