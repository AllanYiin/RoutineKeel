"""Deterministic checks and routing for an agent draft. Never calls a model."""

import copy
import hashlib

from ..contracts import StudioError
from ..governance.evidence import EvidenceRecords
from ..project.models import canonical_json

# Stronger labels need an auditable human or machine record; an agent cannot grant them.
AGENT_LABELS = {"observed", "inferred", "conflicted"}
RESOLUTIONS = {
    "locator_invalid": {"fixed"},
    "conflict_needs_question": {"fixed"},
    "unsupported": {"fixed"},
    "leaves_machine": {"fixed", "keep"},
}


def complete_technical_fields(payload, tools):
    """Fill contract fields a person should not be asked about, from the tool description."""
    if not isinstance(payload, dict) or not isinstance(payload.get("nodes"), list):
        raise StudioError("IR_SCHEMA_INVALID", "草稿必須包含步驟清單。")
    payload = copy.deepcopy(payload)
    payload.setdefault("ir_version", "1.0")
    payload.setdefault("secrets", [])
    payload.setdefault("revision", 1)  # Replaced with the next number when a person commits.
    for node in payload["nodes"]:
        if not isinstance(node, dict):
            raise StudioError("IR_SCHEMA_INVALID", "步驟必須是物件。")
        versions = [t for t in tools.values() if t.tool_id == node.get("tool")]
        if "tool_version" not in node and len(versions) == 1:
            node["tool_version"] = versions[0].version
        tool = tools.get(f"{node.get('tool')}@{node.get('tool_version')}")
        if tool is None:
            raise StudioError("TOOL_NOT_ALLOWED", f"沒有可用工具：{node.get('tool')}")
        node.setdefault("kind", tool.kind)
        node.setdefault("side_effects", tool.side_effects)
        node.setdefault("platform_requirements", list(tool.platforms))
        node.setdefault("resources", tool.resources.model_dump())
        node.setdefault("timeout_seconds", 60)
        node.setdefault("depends_on", [])
        node.setdefault("parameters", {})
        node.setdefault("preconditions", [])
        node.setdefault("retry_policy", {"mode": "never", "max_attempts": 1})
        uncacheable = node["side_effects"] == "external" or node["kind"] == "llm"
        node.setdefault(
            "cache_policy",
            {"mode": "none", "fingerprint_fields": []}
            if uncacheable
            else {
                "mode": "content_addressed",
                "fingerprint_fields": ["inputs", "parameters", "tool_version"],
            },
        )
        if "postconditions" not in node and isinstance(node.get("outputs"), dict):
            node["postconditions"] = [
                {"operator": "exists", "reference": f"artifact://{node.get('id')}/{name}"}
                for name in node["outputs"]
            ]
    return payload


def _item(kind, target, detail):
    identity = hashlib.sha256(f"{kind}\0{target}".encode()).hexdigest()[:16]
    return {"item_id": identity, "kind": kind, "target": target, "detail": detail}


def _locator_problem(studio, project_id, evidence):
    try:
        basis = EvidenceRecords(studio.repository).basis(project_id, evidence)
    except StudioError as exc:
        return str(exc)
    path, _, line = evidence["evidence_locator"].rpartition(":")
    if not path or not line.isdigit():
        return None
    source = studio.sources.get(project_id, basis["source_id"], version=basis["source_version"])
    if not any(record["path"] == path for record in source.manifest["files"]):
        return None  # The colon belongs to the file name itself.
    content = studio.sources.content(project_id, source.source_id, path, version=source.version)
    try:
        lines = len(content.decode("utf-8").splitlines())
    except UnicodeDecodeError:
        return "此檔案不是文字，不能用行號當作位置。"
    if not 1 <= int(line) <= max(lines, 1):
        return f"行號超出範圍；這個檔案共有 {lines} 行。"
    return None


def reflection_items(studio, project_id, ir, tools, questions):
    """Checklist the agent must answer before any card is shown to a person."""
    from ..compiler.compatibility import check_supported

    items = []
    for evidence in ir.evidence:
        problem = _locator_problem(studio, project_id, evidence.model_dump())
        if problem:
            items.append(_item("locator_invalid", evidence.id, problem))
        if evidence.confidence_label == "conflicted" and evidence.id not in questions:
            items.append(
                _item(
                    "conflict_needs_question",
                    evidence.id,
                    "互相矛盾的觀察必須附上給人回答的問題與 2–4 個選項。",
                )
            )
    try:
        check_supported(ir, tools)
    except StudioError as exc:
        items.append(_item("unsupported", "workflow", f"{exc.code}：{exc}"))
    for node in ir.nodes:
        if node.side_effects == "external" or node.resources.network:
            items.append(_item("leaves_machine", node.id, "這個步驟會連線或影響本機以外的資料。"))
    return items


def validate_questions(questions, ir):
    """Options are candidate claims: the chosen text becomes the confirmed rule verbatim."""
    result = {}
    known = {e.id: e for e in ir.evidence}
    for question in questions if isinstance(questions, list) else []:
        options = question.get("options") if isinstance(question, dict) else None
        if (
            not isinstance(question, dict)
            or question.get("evidence_id") not in known
            or not isinstance(question.get("prompt"), str)
            or not question["prompt"].strip()
            or not isinstance(question.get("why"), str)
            or not question["why"].strip()
            or not isinstance(options, list)
            or not 2 <= len(options) <= 4
            or not all(isinstance(o, str) and o.strip() for o in options)
            or len(set(options)) != len(options)
        ):
            raise StudioError(
                "INPUT_INVALID", "問題需指向草稿中的證據，並附說明、原因與 2–4 個不同選項。"
            )
        result[question["evidence_id"]] = {
            "prompt": question["prompt"].strip(),
            "why": question["why"].strip(),
            "options": [o.strip() for o in options],
        }
    return result


def _facts(tool):
    """Comparison facts come from the registered contract, not from agent wording."""
    return {
        "cost_micro_usd": tool.cost_micro_usd,
        "same_result_each_run": tool.kind == "deterministic",
        "needs_person_each_run": tool.kind == "human_approval",
        "platforms": list(tool.platforms),
        "leaves_machine": tool.side_effects == "external" or tool.resources.network,
    }


def _content_hash(value):
    return hashlib.sha256(canonical_json({"content": value}).encode()).hexdigest()


def build_cards(ir, tools, questions, invalid_locators):
    cards = [
        {
            "card_id": "goal",
            "kind": "confirm",
            "target": "goal",
            "text": ir.goal.description,
            "details": list(ir.goal.success_criteria),
            "content_hash": _content_hash(ir.goal.model_dump()),
        }
    ]
    for evidence in ir.evidence:
        base = {
            "card_id": "evidence:" + evidence.id,
            "target": evidence.id,
            "text": evidence.claim,
            "locator": evidence.evidence_locator,
            "content_hash": _content_hash(
                [evidence.claim, evidence.evidence_locator, evidence.source_id]
            ),
        }
        if evidence.confidence_label == "conflicted":
            cards.append({**base, "kind": "question", **questions[evidence.id]})
        elif evidence.confidence_label == "observed" and evidence.id not in invalid_locators:
            cards.append({**base, "kind": "auto"})
        else:
            cards.append({**base, "kind": "confirm"})
    for node in ir.nodes:
        current = tools[f"{node.tool}@{node.tool_version}"]
        # Same ports are not enough: copying and reformatting share a signature. A choice is
        # only worth a person's time when the tools do the same job with different trade-offs.
        options = [
            tool
            for tool in tools.values()
            if tool.inputs == current.inputs
            and tool.outputs == current.outputs
            and set(tool.capabilities) & set(current.capabilities)
        ]
        if len({canonical_json(_facts(tool)) for tool in options}) < 2:
            continue
        cards.append(
            {
                "card_id": "choice:" + node.id,
                "kind": "choice",
                "target": node.id,
                "text": f"「{node.id}」這一步要用哪一種做法？",
                "drafted": f"{current.tool_id}@{current.version}",
                "options": [
                    {
                        "key": f"{tool.tool_id}@{tool.version}",
                        "title": tool.title,
                        "facts": _facts(tool),
                    }
                    for tool in sorted(options, key=lambda t: (t.tool_id, t.version))
                ],
                "content_hash": _content_hash(
                    sorted(f"{tool.tool_id}@{tool.version}" for tool in options)
                ),
            }
        )
    for card in cards:
        card["state"] = "auto" if card["kind"] == "auto" else "pending"
    return cards
