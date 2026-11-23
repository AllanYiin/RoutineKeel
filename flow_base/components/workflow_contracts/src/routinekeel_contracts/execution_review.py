"""Execution checks confirmed in the Studio design review."""

from typing import Literal

from pydantic import Field, JsonValue, ValidationError, model_validator

from .domain_assets import content_hash
from .errors import StudioError
from .graph import ForEach, Join, Repeat, Review, Switch
from .models import Contract, Identifier
from .references import parse_reference


class MachineCheck(Contract):
    id: Identifier
    output: Identifier
    evaluator: Literal["equals", "length", "range"]
    expected: JsonValue = None
    min: int | float | None = None
    max: int | float | None = None
    tolerate: bool = False
    follow_up: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def criteria(self):
        if self.evaluator == "equals":
            if (
                "expected" not in self.model_fields_set
                or self.min is not None
                or self.max is not None
            ):
                raise ValueError("等值檢核須提供 expected")
        elif (
            self.expected is not None
            or (self.min is None and self.max is None)
            or (self.min is not None and self.max is not None and self.min > self.max)
        ):
            raise ValueError("範圍或長度檢核須提供有效門檻")
        if self.tolerate and not self.follow_up.strip():
            raise ValueError("容許偏差須指定後續校閱或修正")
        return self


class NodeReview(Contract):
    machine: list[MachineCheck] = Field(default_factory=list, max_length=40)
    human: Identifier | None = None

    @model_validator(mode="after")
    def checks(self):
        if (not self.machine and not self.human) or len({rule.id for rule in self.machine}) != len(
            self.machine
        ):
            raise ValueError("關鍵節點須有具名且不重複的檢核")
        return self


class ExecutionReview(Contract):
    version: Literal[1] = 1
    mode: Literal["deliver_then_refine", "stop_on_error"]
    scope: str = Field(min_length=1, max_length=1000)
    critical_steps: dict[str, NodeReview]

    @model_validator(mode="after")
    def stop_is_strict(self):
        if self.mode == "stop_on_error" and any(
            rule.tolerate for step in self.critical_steps.values() for rule in step.machine
        ):
            raise ValueError("不能有錯的流程須阻擋檢核失敗")
        return self


def step_paths(graph, prefix=""):
    for node in graph.nodes:
        path = prefix + node.id
        yield path, node
        if isinstance(node, (ForEach, Repeat)):
            yield from step_paths(node.body, path + "/")


def reviewed_graph_hash(graph):
    """Bind the design, excluding only metadata introduced by the export transaction."""
    payload = graph.model_dump(mode="json", exclude_none=True)
    export_metadata = {
        "design_review", "project_id", "interview_revision", "proposal_id",
        "decisions_hash", "skill_sources", "skill_files",
    }
    payload["provenance"] = {
        key: value for key, value in payload["provenance"].items() if key not in export_metadata
    }
    return content_hash(payload)


def validate_execution_review(graph):
    raw = graph.quality_contract.get("execution_review")
    if "design_review" in graph.provenance:
        receipt = graph.provenance["design_review"]
        details = receipt.get("details") if isinstance(receipt, dict) else None
        prefilled = details.get("prefilled_decisions") if isinstance(details, dict) else None
        canonical = graph.model_dump(mode="json", exclude_none=True)
        quality = {
            key: value
            for key, value in graph.quality_contract.items()
            if key != "execution_review"
        }
        if (
            not isinstance(prefilled, dict)
            or receipt.get("sha256") != content_hash(details)
            or raw is None
            or details.get("policy") != raw
            or prefilled.get("quality") != quality
            or prefilled.get("goal") != graph.workflow.goal
            or prefilled.get("evolution") != (
                graph.evolution_policy or {"automatic_promotion": False}
            )
            or any(
                prefilled.get(key) != canonical[field]
                for key, field in (("steps", "nodes"), ("inputs", "inputs"), ("outputs", "outputs"))
            )
        ):
            raise StudioError("GRAPH_INVALID", "工作流圖或檢核與已確認的定稿內容不一致。")
        if "graph_hash" in details and (
            details["graph_hash"] != reviewed_graph_hash(graph)
            or details.get("skill_manifest") != graph.provenance.get("skill_files", {})
            or details.get("skill_sources") != graph.provenance.get("skill_sources", {})
        ):
            raise StudioError("GRAPH_INVALID", "完整工作流契約或技能與定稿指紋不一致。")
    if raw is None and "execution_review" not in graph.quality_contract:
        return
    from .graph_validator import validate_structure
    from .pattern_composition import _successful_dependencies

    try:
        policy = ExecutionReview.model_validate(raw)
    except ValidationError as exc:
        raise StudioError("GRAPH_INVALID", "關鍵檢核或交付模式不符合契約。") from exc

    covered = set()

    def visit(body, prefix=""):
        nodes = {node.id: node for node in body.nodes}
        local = {
            key: review
            for key, review in policy.critical_steps.items()
            if key.startswith(prefix) and "/" not in key[len(prefix) :]
        }
        if any(review.human for review in local.values()):
            dependencies = {}
            order = validate_structure(
                body, max_nodes=401, dependencies=dependencies, extended=True
            )
            required = _successful_dependencies(body, order, dependencies)
        for path, review in local.items():
            key = path[len(prefix) :]
            node = nodes.get(key)
            if node is None or not node.outputs:
                raise StudioError("GRAPH_INVALID", "關鍵檢核須指向有輸出的實際步驟。")
            covered.add(path)
            for rule in review.machine:
                port = node.outputs.get(rule.output)
                types = {"length": {"array", "object", "string"}, "range": {"integer", "number"}}
                if port is None or port.type not in types.get(rule.evaluator, {port.type}):
                    raise StudioError("GRAPH_INVALID", "機器檢核與輸出埠型別不相容。")
            if review.human:
                reviewer = nodes.get(review.human)
                if not isinstance(reviewer, Review) or not any(
                    parse_reference(port.source).owner == key
                    and parse_reference(port.source).scheme == "artifact"
                    for port in reviewer.inputs.values()
                ):
                    raise StudioError("GRAPH_INVALID", "人工節點須收到關鍵步驟的實際輸出。")
                consumers = set()
                for candidate in body.nodes:
                    values = [port.source for port in candidate.inputs.values()]
                    if isinstance(candidate, Switch):
                        values.append(candidate.condition.source)
                    elif isinstance(candidate, Join):
                        values.extend(
                            value
                            for mapping in candidate.branches.values()
                            for value in mapping.values()
                        )
                    elif isinstance(candidate, ForEach):
                        values.append(candidate.items)
                    elif isinstance(candidate, Repeat):
                        values.append(candidate.initial.source)
                    if any(
                        parse_reference(value).scheme == "artifact"
                        and parse_reference(value).owner == key
                        for value in values
                    ):
                        consumers.add(candidate.id)
                direct_output = any(
                    parse_reference(value).owner == key for value in body.outputs.values()
                )
                if direct_output or any(
                    review.human not in required[c] for c in consumers - {review.human}
                ):
                    raise StudioError("GRAPH_INVALID", "關鍵輸出的使用或交付須經指定人工校閱節點。")
        for node in body.nodes:
            if isinstance(node, (ForEach, Repeat)):
                visit(node.body, prefix + node.id + "/")

    visit(graph)
    if covered != policy.critical_steps.keys():
        raise StudioError("GRAPH_INVALID", "關鍵檢核含有不存在的子流程步驟。")


def child_execution_review(contract, child):
    if "execution_review" not in contract:
        return {}
    policy = contract["execution_review"]
    prefix = child + "/"
    checks = {
        key[len(prefix) :]: value
        for key, value in policy["critical_steps"].items()
        if key.startswith(prefix)
    }
    return {"execution_review": {**policy, "critical_steps": checks}}
