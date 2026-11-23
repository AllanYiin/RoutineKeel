"""Present and validate a versioned Studio design before Blueprint export."""

import json
import re
from datetime import date
from urllib.parse import urlsplit

from pydantic import Field, JsonValue, ValidationError
from routinekeel_contracts.execution_review import ExecutionReview, step_paths
from routinekeel_contracts.graph_validator import port_for, validate_blueprint, validate_structure
from routinekeel_contracts.models import Contract
from routinekeel_contracts.references import parse_reference

from ..contracts import StudioError
from .interviews import digest

_MODEL_PARAMETER = re.compile(
    r"temperature|top[_-]?[pk]|beam|sampling|seed|tokens|penalty|whisper|vad", re.I
)
_THRESHOLD = re.compile(
    r"threshold|tolerance|cutoff|minimum|maximum|timeout|^min_|^max_|^limit", re.I
)
_LOCI = {"local_runtime", "host_mediated", "external_service"}


class ResearchSource(Contract):
    query: str = Field(min_length=1, max_length=600)
    url: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=500)
    accessed_on: str
    recommended_value: JsonValue
    applicability: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=1000)


def fail(message):
    raise StudioError("BLUEPRINT_BLOCKED", message)


def _diagram(model, *, final):
    lines = ["flowchart TD"]
    if final:
        lines.extend(['  rk_input["底座 · 檢核輸入"]', '  rk_output["底座 · 檢核與交付輸出"]'])
    labels = {path: "step_" + str(index) for index, (path, _) in enumerate(step_paths(model), 1)}
    checks = model.quality_contract.get("execution_review", {}).get("critical_steps", {})
    if final and checks:
        lines.append('  rk_stop["停止並保存檢核證據"]')

    def render(graph, prefix=""):
        dependencies = {}
        validate_structure(graph, max_nodes=401, dependencies=dependencies, extended=True)
        routes = {}
        for node in graph.nodes:
            if node.kind == "switch":
                for route, targets in node.routes.items():
                    for target in targets:
                        routes[node.id, target] = route
        for node in graph.nodes:
            path = prefix + node.id
            marker = " · 人工等待" if node.kind == "human_review" else ""
            review = checks.get(path, {}) if final else {}
            if review.get("machine"):
                marker += " · 機器檢核"
            if review.get("human"):
                marker += " · 須人工校閱"
            label = "工作流 · " + path + marker if final else path + marker
            lines.append(f'  {labels[path]}["{label}"]')
            if review.get("human") or any(
                not rule.get("tolerate") for rule in review.get("machine", [])
            ):
                lines.append(f"  {labels[path]} -. 檢核未通過 .-> rk_stop")
            if any(rule.get("tolerate") for rule in review.get("machine", [])):
                lines.append(f'  {labels[path]} -. 容許偏差 .-> rk_note["保存待修與校閱事項"]')
            for parent in dependencies[node.id]:
                route = routes.get((parent, node.id))
                link = f"-->|{route}|" if route else "-->"
                lines.append(f"  {labels[prefix + parent]} {link} {labels[path]}")
            if prefix and not dependencies[node.id]:
                lines.append(f"  {labels[prefix[:-1]]} -. 子流程 .-> {labels[path]}")
            if final and not prefix and not dependencies[node.id]:
                lines.append(f"  rk_input --> {labels[path]}")
            if hasattr(node, "body"):
                render(node.body, path + "/")
        if final and not prefix:
            for value in graph.outputs.values():
                lines.append(f"  {labels[parse_reference(value).owner]} --> rk_output")

    render(model)
    return "\n".join(dict.fromkeys(lines))


def _put(result, key, value, category, default_kind, *, port=None):
    result[key] = {
        "value": value,
        "category": category,
        "suggested_kind": default_kind,
        "type": port["type"] if port else type(value).__name__,
        **({"port": port} if port else {}),
    }


def _field_variables(result, prefix, fields, *, model_parameter=False):
    for name, value in fields.items():
        key = prefix + "/" + name.replace("~", "~0").replace("/", "~1")
        if isinstance(value, dict) and value:
            _field_variables(result, key, value, model_parameter=model_parameter)
        else:
            category = "other"
            if type(value) in {int, float}:
                if _MODEL_PARAMETER.search(name) or model_parameter:
                    category = "model_hyperparameter"
                if _THRESHOLD.search(name):
                    category = "threshold"
            _put(result, key, value, category, "fixed")


def inventory(model):
    variables, steps, tools = {}, {}, {}
    for name, port in model.inputs.items():
        _put(
            variables,
            f"input/{name}",
            None,
            "other",
            "run_input",
            port=port.model_dump(mode="json"),
        )
    for path, node in step_paths(model):
        inputs = {name: item.model_dump(mode="json") for name, item in node.inputs.items()}
        outputs = {name: port.model_dump(mode="json") for name, port in node.outputs.items()}
        steps[path] = {
            "kind": node.kind,
            "inputs": inputs,
            "outputs": outputs,
            "depends_on": node.depends_on,
        }
        if node.kind == "join":
            steps[path].update(branches=node.branches, fallbacks=node.fallbacks)
        if node.kind == "switch":
            steps[path]["routes"] = node.routes
        if node.kind == "human_review":
            steps[path]["prompt"] = node.prompt
        for name, port in outputs.items():
            _put(variables, f"output/{path}/{name}", None, "other", "derived", port=port)
        if node.kind == "task":
            tools[path] = {
                "capability": node.capability,
                "version": node.capability_version,
                "parameters": node.parameters,
                "permissions": node.permissions,
                "side_effect": node.side_effect,
            }
            _field_variables(
                variables,
                "parameter/" + path,
                node.parameters,
                model_parameter=node.capability.startswith(("llm.", "model.")),
            )
        if hasattr(node, "condition"):
            value = node.condition.value
            _put(
                variables,
                f"condition/{path}",
                value,
                "threshold" if type(value) in {int, float} else "other",
                "fixed",
            )
            steps[path]["condition"] = node.condition.model_dump(mode="json")
        if hasattr(node, "body"):
            limit = "max_items" if node.kind == "foreach" else "max_iterations"
            _field_variables(
                variables,
                f"limits/{path}",
                {limit: getattr(node, limit), **node.limits.model_dump()},
            )
            steps[path]["iteration_input"] = (
                node.items if node.kind == "foreach" else node.initial.source
            )
    for group in ("evaluators", "invariants"):
        for index, rule in enumerate(model.quality_contract.get(group, [])):
            if isinstance(rule, dict):
                for name in ("min", "max", "expected"):
                    if name in rule:
                        value = rule[name]
                        _put(
                            variables,
                            f"quality/{group}/{index + 1}/{name}",
                            value,
                            "threshold" if type(value) in {int, float} else "other",
                            "fixed",
                        )
    for record in model.provenance.get("pattern_compositions", []):
        gate = record.get("gate")
        if gate and type(gate.get("equals")) in {int, float}:
            _put(
                variables,
                f"pattern_gate/{record['namespace']}/equals",
                gate["equals"],
                "threshold",
                "fixed",
            )
    nodes = {node.id: node for node in model.nodes}
    for name, source in model.outputs.items():
        port = port_for(model, nodes, source).model_dump(mode="json")
        _put(variables, f"delivery/{name}", source, "other", "derived", port=port)
    return steps, variables, tools


def research_requests(variables):
    return [
        {
            "variable": name,
            "value": item["value"],
            "category": item["category"],
            "query_hint": f"{name.rsplit('/', 1)[-1]} recommended settings official documentation",
            "instruction": "使用宿主網頁搜尋查閱原廠、正式標準或原始研究，記錄版本、適用條件及選值理由。",
        }
        for name, item in variables.items()
        if item["category"] != "other"
    ]


def draft_presentation(graph):
    model, _ = validate_blueprint(graph)
    steps, variables, tools = inventory(model)
    return {
        "diagram": _diagram(model, final=False),
        "steps": steps,
        "variables": variables,
        "tools": tools,
        "research_requests": research_requests(variables),
        "next_questions": ["確認內嵌技能及工具配置", "校閱術語及變數", "確認關鍵檢核與交付方式"],
    }


def _check_research(key, value, research):
    try:
        source = ResearchSource.model_validate(research)
        url = urlsplit(source.url)
        date.fromisoformat(source.accessed_on)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or digest({"value": source.recommended_value}) != digest({"value": value})
        ):
            raise ValueError("invalid source URL")
    except (ValidationError, ValueError):
        fail("請為建議的超參數或門檻附可追溯的網路研究紀錄：" + key)
    return source.model_dump(mode="json")


def final_presentation(proposal, review):
    from .blueprints import check_portable

    keys = {"policy", "skills", "tool_locations", "glossary", "variables"}
    if not isinstance(review, dict) or set(review) != keys:
        fail("定稿須包括檢核、技能、工具位置、術語及變數分類。")
    graph = proposal["graph"]
    model, _ = validate_blueprint(graph)
    steps, variables, tools = inventory(model)
    selected = set(proposal["skill_sources"])
    skills = review["skills"]
    if (
        not isinstance(skills, dict)
        or set(skills) != selected
        or any(
            not isinstance(targets, list)
            or not targets
            or any(not isinstance(path, str) or path not in steps for path in targets)
            for targets in skills.values()
        )
    ):
        fail("請明確確認內嵌技能及對應步驟，沒有內嵌技能時提交空物件。")
    locations = review["tool_locations"]
    if (
        not isinstance(locations, dict)
        or locations.keys() != tools.keys()
        or any(not isinstance(locus, str) or locus not in _LOCI for locus in locations.values())
    ):
        fail("請逐項確認工具的執行位置。")
    glossary = review["glossary"]
    if not isinstance(glossary, dict) or any(
        not term.strip() or not isinstance(value, str) or not value.strip()
        for term, value in glossary.items()
    ):
        fail("請提供術語對照，若沒有專業術語可提交空物件。")
    try:
        policy = ExecutionReview.model_validate(review["policy"])
    except ValidationError as exc:
        raise StudioError("BLUEPRINT_BLOCKED", "請確認交付模式、作用範圍及關鍵節點檢核。") from exc
    if not policy.critical_steps:
        fail("請指定至少一個關鍵節點的輸出檢核。")
    for path, checks in policy.critical_steps.items():
        for rule in checks.machine:
            for name in ("min", "max", "expected"):
                value = getattr(rule, name)
                if type(value) in {int, float}:
                    _put(variables, f"check/{path}/{rule.id}/{name}", value, "threshold", "fixed")
    decisions = review["variables"]
    if not isinstance(decisions, dict) or decisions.keys() != variables.keys():
        fail("請逐項確認流程變數（含檢核門檻），分類須涵蓋完整清冊。")
    for name, item in variables.items():
        evidence = decisions[name]
        if not isinstance(evidence, dict) or set(evidence) - {"kind", "unit", "range", "research"}:
            fail("變數需要用途分類及適用的研究紀錄：" + name)
        kind = evidence.get("kind")
        allowed = (
            {item["suggested_kind"]}
            if item["suggested_kind"] in {"run_input", "derived"}
            else {"fixed", "adjustable"}
        )
        if not isinstance(kind, str) or kind not in allowed:
            fail("變數分類與資料來源不一致：" + name)
        if any(
            field in evidence
            and evidence[field] is not None
            and not isinstance(evidence[field], str)
            for field in ("unit", "range")
        ):
            fail("變數的單位與範圍須以可讀文字表示：" + name)
        item.update(kind=kind, unit=evidence.get("unit"), range=evidence.get("range"))
        if item["category"] != "other":
            item["research"] = _check_research(name, item["value"], evidence.get("research"))
    runtime = policy.model_dump(mode="json", exclude_unset=True)
    composed = {
        **graph,
        "quality_contract": {**graph["quality_contract"], "execution_review": runtime},
    }
    final, _ = validate_blueprint(composed)
    presentation = {
        "draft_diagram": proposal["draft_design"]["diagram"],
        "final_diagram": _diagram(final, final=True),
        "steps": steps,
        "variables": variables,
        "tools": {path: {**item, "locus": locations[path]} for path, item in tools.items()},
        "skills": skills,
        "skill_sources": proposal["skill_sources"],
        "glossary": glossary,
        "policy": runtime,
        "prefilled_decisions": proposal["decisions"],
        "review_instruction": "顯示完整內容後，請使用者確認整份定稿或指出待修改項。",
    }
    check_portable(presentation)
    return composed, presentation, digest(presentation)


def finalize(studio, project_id, expected_revision, request_id, draft_id, review):
    def edit(_db, state):
        current = state.get("blueprint_proposal", {})
        if (
            current.get("proposal_id") != draft_id
            or current.get("revision") != expected_revision
            or current.get("status") != "design_review_required"
        ):
            fail("請先展示最新的工作流初稿，定稿須對應同一版本。")
        if state["phase"] == "paused":
            fail("請先恢復訪談。")
        if current["open_design_domains"]:
            fail("定稿前仍缺少決策：" + "、".join(current["open_design_domains"]))
        for name, label in (("rules", "業務規則"), ("success", "成功條件"), ("cases", "驗收案例")):
            if not current["decisions"].get(name):
                fail("定稿缺少" + label + "，請先補入初稿。")
        graph, presentation, proof = final_presentation(current, review)
        final = {
            **current,
            "status": "ready_for_confirmation",
            "draft_id": draft_id,
            "graph": graph,
            "design_review": presentation,
            "review_hash": proof,
            "revision": expected_revision + 1,
        }
        final.pop("proposal_id")
        details = {
            key: value for key, value in presentation.items() if not key.endswith("_diagram")
        }
        final["display_text"] = (
            "請檢視完整定稿並確認。\n\n```mermaid\n"
            + presentation["final_diagram"]
            + "\n```\n\n"
            + json.dumps(details, ensure_ascii=False, indent=2)
        )
        final["proposal_id"] = digest(final)
        state["blueprint_proposal"] = final
        state["phase"] = "needs_confirmation"

    from .blueprints import check_portable

    check_portable(review)
    updated = studio.interviews._change(
        project_id,
        expected_revision,
        request_id,
        ["finalize_workflow_design", draft_id, review],
        edit,
    )
    return updated["blueprint_proposal"]
