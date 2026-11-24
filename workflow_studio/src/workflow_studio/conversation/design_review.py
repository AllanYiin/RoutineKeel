"""Present and validate a versioned Studio design before Blueprint export."""

import json
import re
from datetime import date
from urllib.parse import urlsplit

from pydantic import Field, JsonValue, ValidationError
from routinekeel_contracts.base_steps import (
    BOUNDARIES,
    DESIGN_KEY,
    DOCUMENT_INDEXES,
    TASKS,
    VERSION,
    catalog,
    design_steps,
)
from routinekeel_contracts.execution_review import ExecutionReview, reviewed_graph_hash, step_paths
from routinekeel_contracts.graph_validator import port_for, validate_blueprint, validate_structure
from routinekeel_contracts.models import Contract
from routinekeel_contracts.references import parse_reference

from ..contracts import StudioError
from .interviews import digest
from .research import verify_source

_MODEL_PARAMETER = re.compile(
    r"temperature|top[_-]?[pk]|beam|sampling|seed|tokens|penalty|whisper|vad", re.I
)
_THRESHOLD = re.compile(
    r"threshold|tolerance|cutoff|minimum|maximum|timeout|^min_|^max_|^limit", re.I
)
_LOCI = {"local_runtime", "host_mediated", "external_service", "deferred"}
PRESENTATION_VERSION = 2


class ResearchSource(Contract):
    query: str = Field(min_length=1, max_length=600)
    url: str = Field(min_length=1, max_length=2000)
    title: str = Field(min_length=1, max_length=500)
    accessed_on: str
    recommended_value: JsonValue
    applicability: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=1000)
    excerpt: str = Field(min_length=12, max_length=1500)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class StepDecision(Contract):
    purpose: str = Field(min_length=1, max_length=1500)
    completion: str = Field(min_length=1, max_length=1500)
    critical: bool
    rationale: str = Field(min_length=1, max_length=1000)


def fail(message):
    raise StudioError("BLUEPRINT_BLOCKED", message)


def _diagram(model, *, final):
    lines = ["flowchart TD"]
    if final:
        for graph_id, capability in (
            ("rk_input", "base.input.check"),
            ("rk_documa", "base.document.index"),
            ("rk_output", "base.output.check"),
            ("rk_delivery", "base.delivery.prepare"),
        ):
            lines.append(f'  {graph_id}["底座 · {BOUNDARIES[capability][0]}"]')
        lines.extend(
            [
                "  rk_input -->|含文件| rk_documa",
                "  rk_output -->|符合交付條件| rk_delivery",
                '  rk_output -. 交付未通過 .-> rk_preview["保留可用預覽與評估"]',
            ]
        )
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
            capability = getattr(node, "capability", None)
            if final and capability in TASKS:
                label = f"底座 · {TASKS[capability][0]} · {path}{marker}"
            else:
                label = "工作流 · " + path + marker if final else path + marker
            lines.append(f'  {labels[path]}["{label}"]')
            if review.get("human") or any(
                not rule.get("tolerate") for rule in review.get("machine", [])
            ):
                lines.append(f"  {labels[path]} -. 檢核未通過 .-> rk_stop")
            if any(rule.get("tolerate") for rule in review.get("machine", [])):
                lines.append(f'  {labels[path]} -. 容許偏差 .-> rk_note["保存待修與校閱事項"]')
            for parent in sorted(dependencies[node.id]):
                route = routes.get((parent, node.id))
                link = f"-->|{route}|" if route else "-->"
                lines.append(f"  {labels[prefix + parent]} {link} {labels[path]}")
            if prefix and not dependencies[node.id]:
                lines.append(f"  {labels[prefix[:-1]]} -. 子流程 .-> {labels[path]}")
            if final and not prefix and not dependencies[node.id]:
                lines.extend(
                    [f"  rk_input -->|無文件| {labels[path]}", f"  rk_documa --> {labels[path]}"]
                )
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


def _field_variables(result, prefix, fields, *, model_parameter=False, default_kind="fixed"):
    for name, value in fields.items():
        key = prefix + "/" + name.replace("~", "~0").replace("/", "~1")
        if isinstance(value, dict) and value:
            _field_variables(
                result, key, value, model_parameter=model_parameter, default_kind=default_kind
            )
        else:
            category = "other"
            if type(value) in {int, float}:
                if _MODEL_PARAMETER.search(name) or model_parameter:
                    category = "model_hyperparameter"
                if _THRESHOLD.search(name):
                    category = "threshold"
            _put(result, key, value, category, default_kind)


def inventory(model):
    variables, steps, tools = {}, {}, {}
    for name, port in model.inputs.items():
        _put(
            variables,
            f"input/{name}",
            None,
            "other",
            "derived" if name == DOCUMENT_INDEXES and port.type == "object" else "run_input",
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
            if node.capability_requirement is not None:
                tools[path]["requirement"] = node.capability_requirement.model_dump(
                    mode="json",
                    exclude_none=True,
                )
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
        for path, requirement in record.get("requirements", {}).items():
            constraints = requirement.get("constraints", {})
            if isinstance(constraints, dict):
                _field_variables(variables, f"capability/{record['namespace']}/{path}", constraints)
        gate = record.get("gate")
        if gate and type(gate.get("equals")) in {int, float}:
            _put(
                variables,
                f"pattern_gate/{record['namespace']}/equals",
                gate["equals"],
                "threshold",
                "fixed",
            )
    profile = model.quality_contract.get("personalization", {}).get("parameters", {})
    if isinstance(profile, dict):
        _field_variables(variables, "profile", profile, default_kind="adjustable")
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
        "presentation_version": PRESENTATION_VERSION,
        "diagram": _diagram(model, final=False),
        "base_step_catalog": catalog(),
        "steps": steps,
        "variables": variables,
        "tools": tools,
        "research_requests": research_requests(variables),
        "next_questions": [
            "校閱逐步輸入輸出合約",
            "確認內嵌技能、能力需求及執行限制",
            "校閱術語及變數",
            "確認關鍵檢核與交付方式",
        ],
    }


def step_contract_checklist(graph, steps):
    checklist = {}

    def visit(body, prefix=""):
        consumers = {}

        def record(source, target):
            consumers.setdefault(source, []).append(target)

        for name, source in body["outputs"].items():
            record(source, f"{prefix[:-1] or '工作流'}交付 / {name}")
        for node in body["nodes"]:
            path = prefix + node["id"]
            row = steps[path]
            for name, binding in row["inputs"].items():
                record(binding["source"], f"{path} / {name}")
            references = {}
            if node["kind"] == "switch":
                references["分支判斷"] = node["condition"]["source"]
            if "iteration_input" in row:
                references["迭代輸入"] = row["iteration_input"]
            for route, outputs in row.get("branches", {}).items():
                for name, source in outputs.items():
                    references[f"分支 {route} / {name}"] = source
            for name, source in row.get("fallbacks", {}).items():
                references[f"備援 / {name}"] = source
            for name, source in references.items():
                record(source, f"{path} / {name}")
            checklist[path] = {
                "輸入": row["inputs"],
                "其他資料來源": references,
                "輸出": {},
                **({"完成條件": row["decision"]["completion"]} if "decision" in row else {}),
            }
            if "body" in node:
                visit(node["body"], path + "/")
        for node in body["nodes"]:
            path = prefix + node["id"]
            checklist[path]["輸出"] = {
                name: {"合約": port, "用途": consumers.get(f"artifact://{node['id']}/{name}", [])}
                for name, port in steps[path]["outputs"].items()
            }

    visit(graph)
    return checklist


def review_sections(proposal):
    graph = proposal["graph"]
    decisions = proposal["decisions"]
    draft = proposal["draft_design"]
    final = proposal.get("design_review")
    groups = {
        "scope": (
            "目標、輸入與交付",
            {
                "工作流": graph["workflow"],
                "參與者": decisions["actors"],
                "每次輸入": graph["inputs"],
                "具名交付": graph["outputs"],
                "使用偏好與設定": decisions["preferences"],
            },
        ),
        "process": (
            "工作步驟、分支與復原",
            {
                "逐步輸入輸出合約": step_contract_checklist(graph, (final or draft)["steps"]),
                "所有步驟與資料契約": graph["nodes"],
                "人工校閱": decisions["review"],
                "失敗或中斷後的處理": decisions["recovery"],
                **(
                    {
                        "底座通用步驟與資料契約": final.get("base_steps", {}),
                        "每步用途與完成條件": {
                            path: step["decision"] for path, step in final["steps"].items()
                        },
                    }
                    if final
                    else {}
                ),
            },
        ),
        "implementation": (
            "技能、工具與變數",
            {
                "技能來源": proposal["skill_sources"],
                "內嵌檔案": list(proposal["skill_files"]),
                "採用的 Pattern 與能力需求": graph["provenance"].get("pattern_compositions", []),
                "工具配置及權限": (final or draft)["tools"],
                "變數清冊": (final or draft)["variables"],
                **(
                    {"技能與步驟對應": final["skills"], "術語對照": final["glossary"]}
                    if final
                    else {"待研究的建議值": draft["research_requests"]}
                ),
            },
        ),
        "acceptance": (
            "業務規則、檢查與驗收",
            {
                "業務規則": decisions["rules"],
                "品質契約及驗收案例": graph["quality_contract"],
                "工作流改版策略": decisions["evolution"],
                "資源與費用": decisions["budget"],
                "預檢列出的案例缺口": proposal.get("build_gaps", []),
                "其他仍待答覆的項目": proposal["open_design_domains"],
            },
        ),
    }
    stage = "final" if final else "draft"
    return {
        key: {
            "title": title,
            "display_text": json.dumps(values, ensure_ascii=False, indent=2),
            "sha256": digest(
                {
                    "stage": stage,
                    "project_id": proposal["project_id"],
                    "revision": proposal["revision"],
                    "section": key,
                    "values": values,
                }
            ),
        }
        for key, (title, values) in groups.items()
    }


def review_text(proposal):
    final = proposal.get("design_review")
    diagram = final["final_diagram"] if final else proposal["draft_design"]["diagram"]
    reply = "確認定稿並匯出" if final else "初稿可進入定稿"
    lines = ["待校閱的設計提案", f"```mermaid\n{diagram}\n```"]
    if proposal["confirmed"]:
        lines += [
            "### 已答覆的訪談需求",
            json.dumps(proposal["confirmed"], ensure_ascii=False, indent=2),
        ]
    for section in proposal["review_sections"].values():
        lines += ["### " + section["title"], section["display_text"]]
    lines.append(
        "請逐組校閱以上所有預填內容。想調整時，直接說明要刪減、修改或稍後決定的項目。"
        f"若全數同意，回覆「{reply}」。未答覆的項目仍待校閱。"
    )
    return "\n\n".join(lines)


def require_shown_sections(proposal, shown_sections):
    sections = proposal.get("review_sections")
    if not sections:
        fail("這份設計缺少預填校閱清單，請重新預覽初稿。")
    expected = {key: item["sha256"] for key, item in sections.items()}
    if shown_sections != expected:
        supplied = shown_sections if isinstance(shown_sections, dict) else {}
        missing = [
            item["title"] for key, item in sections.items() if supplied.get(key) != expected[key]
        ]
        if missing:
            fail("請先在對話顯示並請使用者確認本版所有預填內容，缺少：" + "、".join(missing))
        fail("預填校閱清單含其他章節，請重新取得本版清單。")


def design_progress(state):
    proposal = state.get("blueprint_proposal")
    if state["phase"] in {"paused", "archived", "deleted", "exported"}:
        return None
    if not proposal or (
        proposal.get("revision") != state["revision"]
        or proposal.get("draft_design", {}).get("presentation_version") != PRESENTATION_VERSION
    ):
        return {
            "stage": "draft_stale" if proposal else "draft_required",
            "next_action": "prepare_blueprint",
        }
    if proposal.get("status") == "ready_for_confirmation":
        if not proposal.get("draft_confirmation"):
            return {"stage": "draft_stale", "next_action": "prepare_blueprint"}
        return {
            "stage": "final_review_required",
            "next_action": "export_blueprint",
            "proposal_id": proposal["proposal_id"],
            "review_hash": proposal["review_hash"],
        }
    open_domains = proposal.get("open_design_domains", [])
    return {
        "stage": "decisions_required" if open_domains else "draft_review_required",
        "next_action": "reply_batch" if open_domains else "finalize_workflow_design",
        "proposal_id": proposal["proposal_id"],
        "open_design_domains": open_domains,
    }


def _check_research(repository, key, value, research):
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
    verify_source(repository, source)
    return source.model_dump(mode="json")


def final_presentation(repository, proposal, review):
    from .blueprints import check_portable, visible_design

    keys = {"policy", "skills", "tool_locations", "glossary", "variables", "step_decisions"}
    if not isinstance(review, dict) or set(review) != keys:
        fail("定稿須包括各步驟用途與完成條件、檢核、技能、工具位置、術語及變數分類。")
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
        fail("請逐項填寫執行位置限制，待建置時解析可填 deferred。")
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
    step_decisions = review["step_decisions"]
    if not isinstance(step_decisions, dict) or step_decisions.keys() != steps.keys():
        fail("請逐項說明每個步驟的用途、完成條件、關鍵性及原因。")
    try:
        for path, row in steps.items():
            decision = StepDecision.model_validate(step_decisions[path])
            if not all(
                value.strip()
                for value in (decision.purpose, decision.completion, decision.rationale)
            ):
                raise ValueError("missing step decision")
            row["decision"] = decision.model_dump(mode="json")
    except (ValidationError, ValueError) as exc:
        raise StudioError(
            "BLUEPRINT_BLOCKED", "各步驟的用途、完成條件及原因須填入具體內容。"
        ) from exc
    if {
        path for path, row in steps.items() if row["decision"]["critical"]
    } != policy.critical_steps.keys():
        fail("每個判定為關鍵的步驟都須設定機器或人工檢核。")
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
            item["research"] = _check_research(
                repository, name, item["value"], evidence.get("research")
            )
    runtime = policy.model_dump(mode="json", exclude_unset=True)
    composed = {
        **graph,
        "quality_contract": {
            **graph["quality_contract"],
            "execution_review": runtime,
            DESIGN_KEY: VERSION,
        },
        "provenance": {**graph["provenance"], "design": visible_design(proposal["decisions"])},
    }
    if "lineage" in proposal:
        composed["provenance"]["lineage"] = proposal["lineage"]
    final, _ = validate_blueprint(composed)
    presentation = {
        "draft_diagram": proposal["draft_design"]["diagram"],
        "final_diagram": _diagram(final, final=True),
        "base_steps": design_steps(final),
        "steps": steps,
        "variables": variables,
        "tools": {
            path: {
                **item,
                **(
                    {"location_deferred": True}
                    if locations[path] == "deferred"
                    else {"locus": locations[path]}
                ),
            }
            for path, item in tools.items()
        },
        "skills": skills,
        "skill_sources": proposal["skill_sources"],
        "skill_manifest": proposal["skill_files"],
        "graph_hash": reviewed_graph_hash(final),
        "glossary": glossary,
        "policy": runtime,
        "prefilled_decisions": proposal["decisions"],
        "review_instruction": "顯示完整內容後，請使用者確認整份定稿或指出待修改項。",
    }
    check_portable(presentation)
    return composed, presentation, digest(presentation)


def finalize(
    studio,
    project_id,
    expected_revision,
    request_id,
    draft_id,
    review,
    draft_reply=None,
    shown_sections=None,
):
    def edit(_db, state):
        current = state.get("blueprint_proposal", {})
        if (
            current.get("proposal_id") != draft_id
            or current.get("revision") != expected_revision
            or current.get("status") != "design_review_required"
            or current.get("draft_design", {}).get("presentation_version") != PRESENTATION_VERSION
        ):
            fail("請先展示最新的工作流初稿，定稿須對應同一版本。")
        if state["phase"] == "paused":
            fail("請先恢復訪談。")
        if current["open_design_domains"]:
            fail("定稿前仍缺少決策：" + "、".join(current["open_design_domains"]))
        for name, label in (("rules", "業務規則"), ("success", "成功條件"), ("cases", "驗收案例")):
            if not current["decisions"].get(name):
                fail("定稿缺少" + label + "，請先補入初稿。")
        reply = (
            draft_reply.strip().rstrip("。！.!").strip().casefold()
            if isinstance(draft_reply, str)
            else ""
        )
        if reply not in {"初稿可進入定稿", "確認初稿", "同意進入定稿", "approve draft"}:
            fail("請先在對話展示同一版初稿，並轉交使用者明確同意進入定稿的實際回覆。")
        require_shown_sections(current, shown_sections)
        graph, presentation, proof = final_presentation(studio.repository, current, review)
        final = {
            **current,
            "status": "ready_for_confirmation",
            "draft_id": draft_id,
            "draft_confirmation": {
                "draft_id": draft_id,
                "revision": expected_revision,
                "user_text": draft_reply,
                "shown_sections": shown_sections,
            },
            "graph": graph,
            "design_review": presentation,
            "review_hash": proof,
            "revision": expected_revision + 1,
        }
        final.pop("proposal_id")
        final["review_sections"] = review_sections(final)
        final["display_text"] = review_text(final)
        final["proposal_id"] = digest(final)
        state["blueprint_proposal"] = final
        state["phase"] = "needs_confirmation"

    from .blueprints import check_portable

    check_portable(review)
    updated = studio.interviews._change(
        project_id,
        expected_revision,
        request_id,
        ["finalize_workflow_design", draft_id, review, draft_reply, shown_sections],
        edit,
    )
    return updated["blueprint_proposal"]
