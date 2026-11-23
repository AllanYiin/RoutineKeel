"""Design-only recommendation, evidence and versioned conversation decisions."""

import re
from typing import Literal

from pydantic import Field, ValidationError
from routinekeel_contracts.domain_assets import AssetID, AssetRef, CapabilityPort, content_hash
from routinekeel_contracts.graph_validator import compatible
from routinekeel_contracts.models import Contract, Identifier

from ..contracts import StudioError
from ..project.service import now


class TaskContext(Contract):
    intent: str = Field(min_length=1, max_length=4000)
    category: Identifier
    intents: list[Identifier] = Field(default_factory=list)
    available_inputs: dict[Identifier, CapabilityPort] = Field(default_factory=dict)
    facts: list[Identifier] = Field(default_factory=list)
    match_keys: list[Identifier] = Field(default_factory=list)
    privacy: Literal["local_only", "host_mediated", "external"] = "local_only"
    budget_micro_usd: int = Field(default=0, ge=0)
    exclusions: list[AssetID] = Field(default_factory=list)
    trace: list[AssetRef] = Field(default_factory=list, max_length=30)


_PRIVACY = {"local_only": 0, "host_mediated": 1, "external": 2}
_DECISIONS = {"accepted", "rejected", "edited", "deferred"}


def _normalized(text):
    return "".join(re.findall(r"[^\W_]+", text.casefold()))


def _phrase_in(text, example):
    phrase = _normalized(example)
    return bool(phrase) and phrase in _normalized(text)


def _comparable(context):
    groups = {}
    for port in context.available_inputs.values():
        if port.schema_id and port.type in {"array", "object", "file"} and port.required:
            groups[port.schema_id] = groups.get(port.schema_id, 0) + 1
    return any(count >= 2 for count in groups.values())


def _port_gaps(boundary, context):
    available = dict(context.available_inputs)
    gaps = []
    # The same file/source cannot satisfy two independent required input ports.
    inputs = sorted(boundary["inputs"].items(), key=lambda item: item[1].get("schema_id") is None)
    for name, description in inputs:
        port = CapabilityPort.model_validate(description)
        if not port.required:
            continue
        matches = [
            key
            for key, candidate in available.items()
            if compatible(candidate, port)
            and (port.schema_id is None or candidate.schema_id == port.schema_id)
            and (not port.enum or candidate.enum and set(candidate.enum) <= set(port.enum))
        ]
        if matches:
            available.pop(name if name in matches else matches[0])
        else:
            gaps.append(name)
    return gaps


def _cycle(ref, contracts, stack=frozenset(), done=None):
    visiting = set(stack)
    visited = set() if done is None else done
    pending = [(ref, False)]
    while pending:
        node, leaving = pending.pop()
        if leaving:
            visiting.remove(node)
            visited.add(node)
        elif node in visiting:
            return True
        elif node in contracts and node not in visited:
            visiting.add(node)
            pending.append((node, True))
            pending.extend(
                (child, False) for child in contracts[node]["design"].get("suggests", [])
            )
    return False


def rank(registry, context, decisions, *, top_k=5):
    if type(top_k) is not int or not 1 <= top_k <= 10:
        raise StudioError("INPUT_INVALID", "建議清單筆數須介於 1 至 10。")
    metadata = registry.pattern_metadata()
    selected = {
        ref
        for ref, decision in decisions.items()
        if decision["decision"] in {"accepted", "edited"} and not decision.get("needs_review")
    }
    skip = {
        ref
        for ref, decision in decisions.items()
        if decision["decision"] in _DECISIONS and not decision.get("needs_review")
    }
    skip_families = {ref.split("@")[0] for ref in skip}
    pending = [
        row
        for row in metadata
        if row["ref"].split("@")[0] not in skip_families
        and row["ref"] not in context.trace
        and row["ref"].split("@")[0] not in context.exclusions
        and (not row["task_categories"] or context.category in row["task_categories"])
    ]
    contracts = {row["ref"]: registry.inspect_pattern(row["ref"], "L2") for row in pending}
    for ref in selected | set(context.trace):
        if any(item["ref"] == ref for item in metadata):
            contracts[ref] = registry.inspect_pattern(ref, "L2")
    # Read linked contracts only when they participate in cycle detection.
    known = {row["ref"] for row in metadata}
    frontier = list(contracts.values())
    while frontier:
        contract = frontier.pop()
        for ref in contract["design"].get("suggests", []):
            if ref in known and ref not in contracts:
                contracts[ref] = registry.inspect_pattern(ref, "L2")
                frontier.append(contracts[ref])
    selected_ids = {ref.split("@")[0] for ref in selected}
    selected_conflicts = {
        name
        for ref in selected
        if ref in contracts
        for name in contracts[ref]["design"].get("conflicts_with", [])
    }
    gates = {contracts[ref]["design"]["gate"] for ref in selected if ref in contracts}
    measured = []
    for row in pending:
        ref = row["ref"]
        design = contracts[ref]["design"]
        if (
            design["cost_micro_usd"] > context.budget_micro_usd
            or _PRIVACY[design["privacy"]] > _PRIVACY[context.privacy]
            or set(context.intents).intersection(design["excluded_intents"])
            or any(_phrase_in(context.intent, example) for example in design["negative_examples"])
            or _cycle(ref, contracts)
            or selected_ids.intersection(design["conflicts_with"])
            or ref.split("@")[0] in selected_conflicts
            or (design["gate"] is not None and design["gate"] in gates)
        ):
            continue
        intents = sorted(set(context.intents).intersection(design["intents"]))
        positive = [
            text for text in design["positive_examples"] if _phrase_in(context.intent, text)
        ]
        if not intents and not positive:
            continue
        facts = set(context.facts)
        facts.discard("two_comparable_datasets")
        facts.discard("explicit_match_key_or_rule")
        if _comparable(context):
            facts.add("two_comparable_datasets")
        if context.match_keys:
            facts.add("explicit_match_key_or_rule")
        dependencies = contracts[ref]["requirements"]
        if (
            "two_comparable_datasets" in design["prerequisites"]
            or any(rule["capability"] == "data.reconcile" for rule in dependencies)
        ) and "two_comparable_datasets" not in facts:
            continue
        missing = sorted(set(design["prerequisites"]) - facts)
        missing.extend(_port_gaps(contracts[ref]["boundary"], context))
        for policy_ref in contracts[ref]["policy_refs"]:
            policy = registry.get_asset("policy", policy_ref).model
            if (
                ref.split("@")[0] in policy.subjects
                and policy.applicability == "prohibited"
                and set(policy.conditions) <= facts
            ):
                break
        else:
            measured.append(
                {
                    "ref": ref,
                    "hash": row["hash"],
                    "title": row["title"],
                    "gate": design["gate"],
                    "score": (row["priority"] or 0)
                    + 30 * len(intents)
                    + 25 * len(positive)
                    - 15 * len(missing),
                    "reasons": intents + positive,
                    "missing_inputs": sorted(set(missing)),
                    "risk": design["privacy"],
                    "next_question": "是否在目前工作流草稿採用此 Pattern？"
                    if not missing
                    else "請先補齊列出的輸入或適用條件。",
                    "conflicts_with": design["conflicts_with"],
                }
            )
    results = []
    emitted = set(gate for gate in gates if gate)
    alternatives = set()
    blocked = set()
    for entry in sorted(measured, key=lambda item: (-item["score"], item["ref"])):
        family = entry["ref"].split("@")[0]
        gate = entry["gate"]
        if (
            family in alternatives
            or family in blocked
            or gate in emitted
            or alternatives.intersection(entry["conflicts_with"])
        ):
            continue
        results.append(entry)
        alternatives.add(family)
        blocked.update(entry["conflicts_with"])
        if gate:
            emitted.add(gate)
        if len(results) == top_k:
            break
    return {
        "recommendations": results,
        "usage": {
            "model_tokens": 0,
            "metadata_read": len(metadata),
            "contract_reads": len(contracts),
            "graph_reads": 0,
        },
    }


def propose(studio, project_id, expected_revision, request_id, context, *, top_k=5):
    try:
        task = TaskContext.model_validate(context)
    except ValidationError as error:
        raise StudioError("INPUT_INVALID", "任務意圖、來源契約或預算格式無效。") from error
    context_hash = content_hash(task.model_dump(mode="json"))

    def edit(_db, state):
        result = (
            rank(studio.tools.packs, task, state.get("pattern_decisions", {}), top_k=top_k)
            if state.get("pattern_discovery_enabled", True)
            else {
                "recommendations": [],
                "usage": {
                    "model_tokens": 0,
                    "metadata_read": 0,
                    "contract_reads": 0,
                    "graph_reads": 0,
                },
            }
        )
        revision = expected_revision + 1
        for row in result["recommendations"]:
            row["recommendation_id"] = content_hash(
                [
                    state["session_id"],
                    revision,
                    context_hash,
                    row["ref"],
                    row["hash"],
                ]
            )
        state["pattern_recommendations"] = {
            **result,
            "context_hash": context_hash,
            "revision": revision,
        }

    state = studio.interviews._change(
        project_id,
        expected_revision,
        request_id,
        ["pattern_recommendations", context_hash, top_k],
        edit,
    )
    return {**state["pattern_recommendations"], "project_id": project_id}


def decide(
    studio,
    project_id,
    expected_revision,
    request_id,
    recommendation_id,
    decision,
    user_text,
    *,
    message_ref=None,
):
    from ..conversation.facade import check_user_text, message_reference

    check_user_text(user_text)
    if decision not in _DECISIONS:
        raise StudioError("INPUT_INVALID", "請選擇採用、拒絕、調整或暫緩。")
    words = _normalized(user_text)
    if decision == "accepted" and any(
        token in words for token in ("不要", "不採用", "先不要", "holdoff", "donot", "reject")
    ):
        raise StudioError("INPUT_INVALID", "回覆原文與採用決策不一致。")
    reference = message_reference(message_ref, request_id)

    def edit(_db, state):
        shown = state.get("pattern_recommendations", {})
        row = next(
            (
                item
                for item in shown.get("recommendations", [])
                if item["recommendation_id"] == recommendation_id
            ),
            None,
        )
        if shown.get("revision") != expected_revision or row is None:
            raise StudioError("REVISION_CONFLICT", "Pattern 建議或訪談版本已更新。")
        if decision == "accepted" and row["missing_inputs"]:
            raise StudioError("PATTERN_NOT_APPLICABLE", "請先補齊此 Pattern 的必要資料與條件。")
        if not any(
            item["ref"] == row["ref"] and item["hash"] == row["hash"]
            for item in studio.tools.packs.pattern_metadata()
        ):
            raise StudioError("PATTERN_APPROVAL_REQUIRED", "Pattern 已停用或核准內容已變動。")
        state.setdefault("pattern_decisions", {})[row["ref"]] = {
            "decision": decision,
            "pattern_hash": row["hash"],
            "context_hash": shown["context_hash"],
            "recommendation_id": recommendation_id,
            "user_text": user_text,
            "message_ref": reference,
            "decided_at": now(),
        }

    state = studio.interviews._change(
        project_id,
        expected_revision,
        request_id,
        ["pattern_decision", recommendation_id, decision, user_text, reference],
        edit,
    )
    return {"revision": state["revision"], "decisions": state["pattern_decisions"]}


def inspect(studio, pattern_ref, level, project_id=None):
    if level == "L3":
        if project_id is None:
            raise StudioError("PATTERN_APPROVAL_REQUIRED", "請先在草稿中選用此 Pattern。")
        row = studio.interviews.get(project_id).get("pattern_decisions", {}).get(pattern_ref, {})
        if row.get("decision") not in {"accepted", "edited"} or row.get("needs_review"):
            raise StudioError("PATTERN_APPROVAL_REQUIRED", "請先核對並選用此 Pattern。")
    return studio.tools.packs.inspect_pattern(pattern_ref, level)


def configure(studio, project_id, expected_revision, request_id, enabled):
    if type(enabled) is not bool:
        raise StudioError("INPUT_INVALID", "請指定是否啟用 Pattern 設計建議。")

    def edit(_db, state):
        state["pattern_discovery_enabled"] = enabled

    state = studio.interviews._change(
        project_id, expected_revision, request_id, ["pattern_discovery_enabled", enabled], edit
    )
    return {"revision": state["revision"], "enabled": state["pattern_discovery_enabled"]}


def measure(registry, examples, *, top_k=3):
    total = matched = possible = misfires = negative = model_tokens = relevant = repeated = 0
    for task, expected in examples:
        context = TaskContext.model_validate(task)
        result = rank(registry, context, {}, top_k=top_k)
        observed = {row["ref"] for row in result["recommendations"]}
        truth = set(expected)
        total += 1
        matched += len(observed & truth)
        possible += len(observed)
        relevant += len(truth)
        negative += not truth
        misfires += bool(observed) and not truth
        model_tokens += result["usage"]["model_tokens"]
        ignored = rank(
            registry, context, {ref: {"decision": "rejected"} for ref in observed}, top_k=top_k
        )
        repeated += sum(row["ref"] in observed for row in ignored["recommendations"])
    return {
        "cases": total,
        "top_k": top_k,
        "precision_at_k": matched / possible if possible else 0,
        "recall_at_k": matched / relevant if relevant else 0,
        "false_trigger_rate": misfires / negative if negative else 0,
        "repeated_rejection_rate": repeated / possible if possible else 0,
        "model_tokens": model_tokens,
    }
