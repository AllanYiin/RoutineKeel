"""Prepare actionable binding proposals while preserving execution authorization."""

import json
import shutil
from importlib import metadata
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, ValidationError, field_validator
from routinekeel_contracts.base_steps import MODEL_STEPS
from routinekeel_contracts.graph_validator import validate_blueprint
from routinekeel_contracts.models import Contract, Identifier
from routinekeel_contracts.pattern_composition import graph_requirements, graph_task_paths
from routinekeel_contracts.privacy import contains_private_data

from ..outputs import reject_links
from ..runtime import FlowError, digest
from .source import require


class BindingOption(Contract):
    title: str = Field(min_length=1, max_length=160)
    approach: Literal["local_tool", "skill_code", "local_implementation", "host_model", "host_tool"]
    plan: str = Field(min_length=1, max_length=1400)
    sources: list[str] = Field(default_factory=list, max_length=5)
    executables: list[Identifier] = Field(default_factory=list, max_length=6)
    distributions: list[Identifier] = Field(default_factory=list, max_length=6)
    skills: list[Identifier] = Field(default_factory=list, max_length=5)

    @field_validator("sources")
    @classmethod
    def public_sources(cls, values):
        for value in values:
            target = urlsplit(value)
            if (
                len(value) > 500 or target.scheme != "https" or not target.hostname
                or target.username or target.password or target.query
            ):
                raise ValueError("use public HTTPS source links without credentials or query data")
        return values


class BindingProposal(Contract):
    gap_id: str
    scope_hash: str
    options: list[BindingOption] = Field(min_length=1, max_length=8)


class BindingSelection(Contract):
    gap_id: str
    scope_hash: str
    option_id: str


def _guide(root, graph):
    workflow = graph["workflow"]["id"]
    path = root / "blueprints" / "binding-guides" / f"{workflow}.json"
    try:
        reject_links(path)
        if not path.is_file() or path.stat().st_size > 128 * 1024:
            return {}
        guide = json.loads(path.read_bytes())
        capabilities = guide.get("capabilities")
        if (
            guide.get("workflow") == workflow and guide.get("version") == 1
            and isinstance(capabilities, dict)
        ):
            return capabilities
    except (FlowError, OSError, ValueError, AttributeError):
        pass
    return {}


def _applicable(option, node, rule, offline, skills):
    if not set(option.skills) <= set(skills):
        return False
    local = option.approach in {"local_tool", "skill_code", "local_implementation"}
    if not local and option.approach == "host_model":
        if any(item.port.type == "file" for item in node.inputs.values()) or any(
            port.type == "file" for port in node.outputs.values()
        ):
            return False
    if not local and (offline or (rule and rule.constraints.offline_only)):
        return False
    locus = "local_runtime" if local else "host_mediated"
    return rule is None or (
        rule.portability == "flexible"
        and (not rule.execution_loci or locus in rule.execution_loci)
    )


def _installed(option):
    evidence = [
        {"kind": "executable", "name": name, "present": shutil.which(name) is not None}
        for name in option.executables
    ]
    for name in option.distributions:
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            version = None
        evidence.append({
            "kind": "python_distribution", "name": name,
            "present": version is not None, "version": version,
        })
    return evidence


def _option_row(option, evidence):
    body = option.model_dump(mode="json")
    ready = bool(evidence) and all(row["present"] for row in evidence)
    missing = [row["name"] for row in evidence if not row["present"]]
    status = "local_detected" if ready else {
        "local_tool": "needs_local_probe",
        "skill_code": "needs_implementation_check",
        "local_implementation": "needs_implementation",
        "host_model": "needs_host_binding",
        "host_tool": "needs_host_observation",
    }[option.approach]
    return {
        **body,
        "id": digest(body),
        "status": status,
        "local_evidence": evidence,
        "missing_dependencies": missing,
        "preparation_requires_approval": bool(missing),
        "action": {
            "local_tool": "prepare_local_dependencies" if missing else "prepare_local_adapter",
            "skill_code": "reuse_skill_code",
            "local_implementation": "implement_local_step",
            "host_model": "prepare_host_model",
            "host_tool": "observe_host_tool",
        }[option.approach],
    }


def _options(raw, node, rule, offline, skills):
    rows = {}
    for value in raw:
        try:
            option = BindingOption.model_validate(value)
        except ValidationError:
            continue
        if _applicable(option, node, rule, offline, skills) and not contains_private_data(value):
            row = _option_row(option, _installed(option))
            rows[row["id"]] = row
    return list(rows.values())


def describe(root, graph, state, gaps):
    from ..graph.capabilities import capability_observation
    from .completion_preparation import preparation_candidates

    model, _ = validate_blueprint(graph)
    tasks, rules = graph_task_paths(model), graph_requirements(model)
    skills = graph["provenance"].get("design_review", {}).get("details", {}).get("skills", {})
    guide = _guide(root, graph)
    discovered = state.get("binding_research", {})
    catalog = None
    for gap in gaps:
        key = gap["location"]
        if gap["kind"] != "capability" or key not in tasks or gap["code"] == "WAITING_FOR_OTHER_BINDINGS":
            continue
        node, rule = tasks[key], rules.get(key)
        assigned = sorted(name for name, steps in skills.items() if key in steps)
        guidance = guide.get(node.capability)
        guidance = guidance if isinstance(guidance, dict) else {}
        configured = guidance.get("options")
        raw = [*(configured if isinstance(configured, list) else []), *discovered.get(key, [])]
        if node.capability in MODEL_STEPS:
            raw.append({
                "title": "使用目前宿主模型", "approach": "host_model",
                "plan": "由宿主接通本節點的具名文字輸入輸出，核對資料範圍及模型用量後取得資格證據。",
            })
        options = _options(raw, node, rule, state["offline"], assigned)
        if rule and any(item.get("recipe_id") for item in gap.get("candidate_rejections", [])):
            if catalog is None:
                catalog, _ = capability_observation(root)
            eligible = preparation_candidates(node, rule, gap["candidate_rejections"], catalog, state["offline"])
            for op_id, (adapter, recipe) in eligible.items():
                option = {
                    "id": digest([adapter.ref, op_id]), "title": adapter.tool,
                    "approach": "observed_preparation", "adapter": adapter.ref, "locus": "local_runtime",
                    "status": "preparable", "action": "prepare_adapter", "recipe_id": recipe.id,
                    "preparation_id": op_id, "selectable": not gap.get("requires_design_revision"),
                }
                downloads = [wheel.model_dump(mode="json") for wheel in getattr(recipe, "wheels", []) if wheel.source]
                if downloads:
                    option["download_sources"] = downloads
                    option["download_allowed"] = not state["offline"] and catalog[adapter.tool].get("download_allowed") is True
                options.append(option)
        for candidate in gap.get("candidates", []):
            candidate_id = digest([candidate["adapter"], candidate["environment_fingerprint"]])
            options.append({
                "id": candidate_id,
                "title": candidate["tool"],
                "approach": "observed_adapter",
                "adapter": candidate["adapter"],
                **({"preparation_id": candidate["preparation_id"]} if candidate.get("preparation_id") else {}),
                "locus": candidate["locus"],
                "status": "observed" if candidate["allowed"] else "needs_permission",
                "action": "select_adapter",
                "selectable": candidate["allowed"] and not candidate.get("declined")
                and not gap.get("requires_design_revision"),
            })
        automatic = guidance.get("handled_by")
        automatic_approaches = ("host_model", "skill_code", "local_implementation")
        if automatic not in automatic_approaches or not any(
            row["approach"] == automatic for row in options
        ):
            automatic = None
        proposed = [row for row in options if row["approach"] != "observed_adapter"]
        if not automatic and len(proposed) == 1 and proposed[0]["approach"] in automatic_approaches:
            automatic = proposed[0]["approach"]
        options.sort(key=lambda row: (
            row.get("locus") != "local_runtime" or row["status"] != "observed",
            row["status"] != "local_detected",
            row["status"] not in {"observed", "local_detected"},
        ))
        gap["binding_options"] = {
            "handled_by": automatic or "tool_selection",
            "contract": {
                "capability": node.capability,
                "version": rule.version or rule.version_range if rule else node.capability_version,
                "inputs": {name: entry.port.model_dump(exclude_none=True) for name, entry in node.inputs.items()},
                "outputs": {name: port.model_dump(exclude_none=True) for name, port in node.outputs.items()},
                "loci": rule.execution_loci if rule else [],
                "side_effect": node.side_effect,
                "permissions": node.permissions,
                "parameters": node.parameters,
                "constraints": rule.constraints.model_dump(exclude_none=True) if rule else {},
            },
            "skills": assigned,
            "options": options,
        }
        if not options or not any(row.get("selectable", True) for row in options):
            gap["actions"].append("research_binding_options")
        elif automatic:
            gap["actions"].insert(0, "continue_" + automatic)
        elif any(row.get("selectable", True) for row in options):
            gap["actions"].append("select_bindings")
    return tasks, rules


def binding_plan(gaps, choices, decisions):
    plan = {"tool_selection": [], "automatic_work": [], "research_required": [], "qualified": []}
    by_node = {row["node"]: row for row in decisions}
    for gap in gaps:
        if gap["code"] == "WAITING_FOR_OTHER_BINDINGS":
            plan["qualified"].append({"node": gap["location"], "adapter": gap["qualification_report"]["adapter"],
                                      "verdict": "PASS"})
            continue
        info = gap.get("binding_options")
        if info is None:
            continue
        key = gap["location"]
        options = info["options"]
        chosen = choices.get(key, {})
        current = next((item for item in options if item["id"] == chosen.get("option_id")), None)
        if current is None and chosen.get("preparation_id"):
            current = next((item for item in options if item["approach"] == "observed_adapter"
                            and item["adapter"] == chosen["adapter"]
                            and item.get("preparation_id") == chosen["preparation_id"]), None)
        automatic = info["handled_by"] != "tool_selection"
        evidence_work = any(action in gap["actions"] for action in (
            "complete_adapter_cases", "provide_host_qualification",
        ))
        auto_adapters = by_node.get(key, {}).get("automatic_candidates", [])
        selectable = [item for item in options if item.get("selectable", True)]
        auto_ready = len(auto_adapters) == 1 and any(
            row.get("adapter") == auto_adapters[0] for row in selectable
        )
        if automatic or current or auto_ready or evidence_work:
            group = "automatic_work"
        else:
            group = "tool_selection" if selectable else "research_required"
        recommended = current or next(
            (row for row in selectable if row["approach"] == info["handled_by"]),
            selectable[0] if selectable else None,
        )
        plan[group].append({
            "node": key, "gap_id": gap["id"], "scope_hash": gap["scope_hash"],
            "actions": gap["actions"], **info,
            "recommended_option_id": recommended["id"] if recommended else None,
            "selection": (
                {**chosen, "next_action": current["action"]} if current else None
            ),
        })
    return plan


def research(state, rows):
    proposals = dict(state.get("binding_research", {}))
    gaps = {row["id"]: row for row in state["gaps"]}
    require(isinstance(rows, list) and 0 < len(rows) <= 50, "請提供至少一項具體工具提案。")
    for raw in rows:
        try:
            entry = BindingProposal.model_validate(raw)
        except ValidationError as error:
            raise FlowError("INVALID_CONFIGURATION", "工具提案格式無效。") from error
        gap = gaps.get(entry.gap_id, {})
        info = gap.get("binding_options")
        require(info and gap["scope_hash"] == entry.scope_hash, "請重讀此能力的目前補強範圍。")
        key = gap["location"]
        body = [item.model_dump(mode="json") for item in entry.options]
        require(
            not contains_private_data(body)
            and all(item.sources or item.skills or item.executables or item.distributions
                    or item.approach == "host_model" for item in entry.options),
            "工具提案須附本機元件、技能來源或公開文件。",
        )
        unique = {digest(item): item for item in [*proposals.get(key, []), *body]}
        require(len(unique) <= 12, "這個能力的工具提案已達上限。")
        proposals[key] = list(unique.values())
    return {**state, "binding_research": proposals}
