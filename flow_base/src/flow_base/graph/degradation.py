"""Approve an explicit new Blueprint variant for a declared optional fallback."""

import json
import sqlite3
from contextlib import closing

from packaging.version import Version
from routinekeel_contracts.domain_assets import content_hash
from routinekeel_contracts.graph_validator import compatible, port_for

from ..core.lifecycle import utc_now
from ..runtime import FlowError, canonical
from .portable import graph_requirements
from .qualification import BindingReview, _source, graph_fingerprint


def describe_degradation(workspace, original_id, variant_id):
    source = _source(workspace, original_id)
    variant = _source(workspace, variant_id)
    declaration = variant.provenance.get("degraded_delivery")
    if (
        not isinstance(declaration, dict)
        or declaration.keys() != {"source_hash", "omissions"}
        or declaration["source_hash"] != original_id
        or source.workflow.id != variant.workflow.id
        or Version(variant.workflow.version) <= Version(source.workflow.version)
        or variant.workflow.revision <= source.workflow.revision
    ):
        raise FlowError("OPTIONAL_DECISION_REQUIRED", "請另建同一工作流的明確降級版本。")
    before = graph_requirements(source)
    after = graph_requirements(variant)
    omissions = declaration["omissions"]
    if not isinstance(omissions, dict) or not omissions:
        raise FlowError("OPTIONAL_DECISION_REQUIRED", "請指明要省略的選用節點與降級契約。")
    for key, fallback in omissions.items():
        if (
            key not in before
            or before[key].required
            or not fallback
            or before[key].fallback_contract != fallback
        ):
            raise FlowError("OPTIONAL_DECISION_REQUIRED", "僅可按既有契約省略選用能力。")
        if key in after:
            raise FlowError("OPTIONAL_DECISION_REQUIRED", "降級版須明確替換原有選用節點。")
    if any(
        key not in after or after[key] != rule
        for key, rule in before.items()
        if key not in omissions
    ):
        raise FlowError("OPTIONAL_DECISION_REQUIRED", "降級不得省略或改寫其他已宣告的能力需求。")
    former_gates = {
        row["namespace"]: row["gate"]
        for row in source.provenance.get("pattern_compositions", [])
        if row.get("gate")
    }
    retained_gates = {
        row["namespace"]: row["gate"]
        for row in variant.provenance.get("pattern_compositions", [])
        if row.get("gate")
    }
    if any(retained_gates.get(key) != value for key, value in former_gates.items()):
        raise FlowError("OPTIONAL_DECISION_REQUIRED", "降級不得省略或放寬必要品質 Gate。")
    old_nodes, new_nodes = ({node.id: node for node in graph.nodes} for graph in (source, variant))
    if (
        source.outputs.keys() != variant.outputs.keys()
        or any(
            not compatible(
                port_for(variant, new_nodes, variant.outputs[name]),
                port_for(source, old_nodes, source.outputs[name]),
            )
            for name in source.outputs
        )
        or any(
            variant.quality_contract.get(key) != value
            for key, value in source.quality_contract.items()
            if key in {"invariants", "evaluators", "required_checks", "review_policy"}
        )
        or not variant.quality_contract.get("golden_cases")
    ):
        raise FlowError(
            "OPTIONAL_DECISION_REQUIRED", "降級版須保留交付契約及必要品質規則並提供測例。"
        )
    details = {
        "source_hash": original_id,
        "variant_hash": variant_id,
        "graph_hash": graph_fingerprint(variant),
        "workflow": f"{variant.workflow.id}@{variant.workflow.version}",
        "omissions": omissions,
    }
    return {**details, "request_hash": content_hash(details)}


def approve_degradation(workspace, original_id, variant_id, review):
    proposal = describe_degradation(workspace, original_id, variant_id)
    if (
        not isinstance(review, BindingReview)
        or review.request_hash != proposal["request_hash"]
        or not review.evidence.strip()
    ):
        raise FlowError("USER_CONFIRMATION_REQUIRED", "請核准具體的替代交付版本。")
    example = {**proposal, "review_hash": content_hash([proposal["request_hash"], review.evidence])}
    scope = content_hash(example)
    workspace.runtime.initialize()
    with closing(workspace.runtime.connect()) as db, db:
        db.execute(
            "INSERT OR IGNORE INTO approved_degraded_blueprints VALUES (?,?,?,?)",
            (variant_id, scope, canonical(example), utc_now()),
        )
    return {
        "status": "degraded_candidate",
        "blueprint_hash": variant_id,
        "approval_hash": scope,
        "optional_disabled": sorted(proposal["omissions"]),
    }


def check_degradation(workspace, source_hash, declaration):
    if declaration is None:
        return
    try:
        workspace.runtime.initialize()
        with closing(workspace.runtime.connect()) as db:
            row = db.execute(
                "SELECT body,approval_hash FROM approved_degraded_blueprints WHERE blueprint_hash=?",
                (source_hash,),
            ).fetchone()
        if row:
            approval = json.loads(row[0])
            if (
                content_hash(approval) == row[1]
                and approval["variant_hash"] == source_hash
                and approval["source_hash"] == declaration["source_hash"]
                and approval["omissions"] == declaration["omissions"]
            ):
                return
    except (ValueError, KeyError, TypeError, sqlite3.Error):
        pass
    raise FlowError("OPTIONAL_DECISION_REQUIRED", "此降級版本需要確切的本機核准收據。")
