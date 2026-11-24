"""Versioned Flow Base steps available to Blueprint v2 authors."""

from copy import deepcopy
from typing import Annotated, Literal

from pydantic import Field, ValidationError, model_validator

from .errors import StudioError
from .graph import GraphPort, Task
from .models import Contract, Identifier
from .references import parse_reference

VERSION = 1
DESIGN_KEY = "base_steps_version"
DOCUMENT_INDEXES = "document_indexes"
RECONCILE_PLAN = "base.reconciliation.plan"
RECONCILE_RUN = "base.reconciliation.run"
DOCUMENT_SEARCH = "base.document.search"
SKILL_LOAD = "base.skill.load"
JUDGE = "base.model.judge"
CRITIC = "base.model.critic"
REVISER = "base.model.reviser"
MODEL_STEPS = frozenset({JUDGE, CRITIC, REVISER})

BOUNDARIES = {
    "base.input.check": (
        "檢核輸入",
        {"inputs": "object", "ports": "object"},
        {"inputs": "object", "inputs_hash": "string"},
    ),
    "base.document.index": (
        "建立文件索引",
        {"documents": "array"},
        {DOCUMENT_INDEXES: "object"},
    ),
    "base.output.check": (
        "檢核輸出",
        {"outputs": "object", "quality_contract": "object"},
        {"assessment_id": "string", "result_status": "string", "delivery_status": "string"},
    ),
    "base.delivery.prepare": (
        "整理交付結果",
        {"outputs": "object", "assessment_id": "string"},
        {"links": "string", "link_index": "string", "archive": "string", "manifest": "string"},
    ),
}

TASKS = {
    RECONCILE_PLAN: ("擬定勾稽計畫", {}, {"plan": "object"}),
    RECONCILE_RUN: (
        "執行勾稽",
        {"plan": "object", "left": "json", "right": "json"},
        {"matched": "boolean", "report": "object"},
    ),
    DOCUMENT_SEARCH: (
        "查詢文件索引",
        {"indexes": "object", "query": "string"},
        {"matches": "array"},
    ),
    SKILL_LOAD: ("載入技能", {"task": "string"}, {"bundle": "object"}),
    JUDGE: (
        "模型審查",
        {"candidate": "json", "rubric": "object"},
        {"verdict": "object"},
    ),
    CRITIC: (
        "批評者",
        {"candidate": "json", "rubric": "object"},
        {"critique": "object"},
    ),
    REVISER: (
        "修訂者",
        {"candidate": "json", "critique": "object"},
        {"candidate": "json"},
    ),
}


class ReconciliationRule(Contract):
    id: Identifier
    operation: Literal["equals", "count", "sum", "keyed"] = "equals"
    left: str = Field(max_length=512)
    right: str = Field(max_length=512)
    left_field: str = Field(default="", max_length=512)
    right_field: str = Field(default="", max_length=512)
    left_key: str = Field(default="", max_length=512)
    right_key: str = Field(default="", max_length=512)
    tolerance: str = Field(default="0", pattern=r"^[0-9]{1,24}(?:\.[0-9]{1,12})?$")

    @model_validator(mode="after")
    def pointers(self):
        import re

        for pointer in (
            self.left,
            self.right,
            self.left_field,
            self.right_field,
            self.left_key,
            self.right_key,
        ):
            if pointer and (
                not pointer.startswith("/") or re.search(r"~(?![01])", pointer)
            ):
                raise ValueError("請使用 JSON Pointer 選取勾稽值")
        if self.operation not in {"sum", "keyed"} and (
            self.left_field or self.right_field
        ):
            raise ValueError("只有加總與逐鍵比對可指定各列的欄位")
        if self.operation == "keyed" and not (self.left_key and self.right_key):
            raise ValueError("逐鍵比對須宣告兩側的資料列鍵值")
        if self.operation != "keyed" and (self.left_key or self.right_key):
            raise ValueError("鍵值僅適用於逐鍵比對")
        if self.operation == "equals" and self.tolerance != "0":
            raise ValueError("比較資料相等時不使用數值容差")
        return self


class ReconciliationPlan(Contract):
    rules: list[ReconciliationRule] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({rule.id for rule in self.rules}) != len(self.rules):
            raise ValueError("勾稽規則識別碼須唯一")
        return self


class ModelReview(Contract):
    passed: bool
    rationale: str = Field(min_length=1, max_length=4000)
    findings: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(
        max_length=50
    )

    @model_validator(mode="after")
    def actionable(self):
        if not self.passed and not any(item.strip() for item in self.findings):
            raise ValueError("未通過的審查須指出待修事項")
        return self


class SkillSelection(Contract):
    skill_names: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=20
    )
    max_tokens: int | None = Field(default=None, gt=0, le=100000)


class DocumentQuery(Contract):
    route: Literal["auto", "lexical-first", "graph-first", "overview"] = "auto"


PARAMETERS = {
    RECONCILE_PLAN: ReconciliationPlan,
    SKILL_LOAD: SkillSelection,
    DOCUMENT_SEARCH: DocumentQuery,
}


def catalog():
    return deepcopy(
        {
            "version": VERSION,
            "boundaries": {
                name: {
                    "title": title,
                    "version": VERSION,
                    "inputs": inputs,
                    "outputs": outputs,
                }
                for name, (title, inputs, outputs) in BOUNDARIES.items()
            },
            "tasks": {
                name: {
                    "title": title,
                    "version": "1",
                    "inputs": inputs,
                    "outputs": outputs,
                    **(
                        {"parameter_schema": PARAMETERS[name].model_json_schema()}
                        if name in PARAMETERS
                        else {}
                    ),
                    **(
                        {"review_schema": ModelReview.model_json_schema()}
                        if name in {JUDGE, CRITIC}
                        else {}
                    ),
                }
                for name, (title, inputs, outputs) in TASKS.items()
            },
        }
    )


def design_steps(graph):
    from .execution_review import step_paths

    known = catalog()
    return {
        "version": VERSION,
        "boundaries": known["boundaries"],
        "tasks": {
            path: {"capability": node.capability, **known["tasks"][node.capability]}
            for path, node in step_paths(graph)
            if isinstance(node, Task) and node.capability in TASKS
        },
    }


def task_node(
    capability, node_id, sources, *, parameters=None, permissions=(), side_effect="none"
):
    """Create a reference whose data ports match the shared catalog."""
    _, inputs, outputs = TASKS[capability]
    return Task(
        id=node_id,
        kind="task",
        capability=capability,
        capability_version="1",
        inputs={
            key: {"source": sources[key], "port": {"type": kind}}
            for key, kind in inputs.items()
        },
        outputs={key: {"type": kind} for key, kind in outputs.items()},
        permissions=list(permissions),
        side_effect=side_effect,
        parameters=parameters or {},
    ).model_dump(mode="json", exclude_none=True)


def validate_base_steps(graph):
    phase_version = graph.quality_contract.get(DESIGN_KEY)
    if DESIGN_KEY in graph.quality_contract and (
        type(phase_version) is not int or phase_version != VERSION
    ):
        raise StudioError("GRAPH_INVALID", "不支援的底座通用步驟版本")
    if (
        phase_version is not None
        and DOCUMENT_INDEXES in graph.inputs
        and graph.inputs[DOCUMENT_INDEXES] != GraphPort(type="object")
    ):
        raise StudioError("GRAPH_INVALID", "底座 document_indexes 須使用必要 object 埠")

    def visit(body):
        tasks = {node.id: node for node in body.nodes if isinstance(node, Task)}
        for node in body.nodes:
            if hasattr(node, "body"):
                visit(node.body)
            if not isinstance(node, Task) or node.capability not in TASKS:
                continue
            _, inputs, outputs = TASKS[node.capability]
            if (
                node.capability_version != "1"
                or {key: value.port for key, value in node.inputs.items()}
                != {key: GraphPort(type=kind) for key, kind in inputs.items()}
                or node.outputs
                != {key: GraphPort(type=kind) for key, kind in outputs.items()}
            ):
                raise StudioError(
                    "GRAPH_INVALID", "底座通用步驟的資料埠或版本不符：" + node.id
                )
            if node.capability in PARAMETERS:
                try:
                    PARAMETERS[node.capability].model_validate(node.parameters)
                except ValidationError as exc:
                    raise StudioError(
                        "GRAPH_INVALID", "底座通用步驟的參數無效：" + node.id
                    ) from exc
            elif node.parameters and node.capability not in MODEL_STEPS:
                raise StudioError(
                    "GRAPH_INVALID", "底座通用步驟不接受額外參數：" + node.id
                )
            if node.capability not in MODEL_STEPS and (
                node.permissions or node.side_effect != "none"
            ):
                raise StudioError(
                    "GRAPH_INVALID", "本機通用步驟的權限宣告不符：" + node.id
                )
            if node.capability == REVISER:
                ref = parse_reference(node.inputs["critique"].source)
                critic = tasks.get(ref.owner) if ref.scheme == "artifact" else None
                if (
                    critic is None
                    or critic.capability != CRITIC
                    or "candidate" not in critic.inputs
                    or critic.inputs["candidate"].source
                    != node.inputs["candidate"].source
                ):
                    raise StudioError(
                        "GRAPH_INVALID", "修訂者須承接同一份候選成果的批評者輸出"
                    )

    visit(graph)
