"""Separately measure contract conformance and behavior for a selected local Adapter."""

import json
import subprocess
import sys
from dataclasses import dataclass

from pydantic import Field, JsonValue, ValidationError
from routinekeel_contracts.bindings import (
    EnvironmentBinding,
    EnvironmentLock,
    ImplementationAdapter,
)
from routinekeel_contracts.domain_assets import canonical_json, content_hash
from routinekeel_contracts.graph import ForEach, Repeat, Task
from routinekeel_contracts.graph_validator import validate_blueprint
from routinekeel_contracts.models import Contract, Identifier
from routinekeel_contracts.privacy import contains_private_data

from ..blueprints.installer import draft_dir, read_archive
from ..blueprints.source import inspect_blueprint
from ..execution.process import worker_environment
from ..runtime import FlowError
from .locking import persist_lock, write_receipts
from .portable import graph_requirements, local_entry, resolve_requirement


@dataclass(frozen=True)
class BindingReview:
    """Confirmation from the local operator adapter, bound to the shown request."""

    request_hash: str
    evidence: str


class AdapterCase(Contract):
    id: Identifier
    inputs: dict[Identifier, JsonValue]
    expected: dict[Identifier, JsonValue] | None = None
    expected_error: str | None = Field(default=None, max_length=80)


def _tasks(graph):
    tasks = {}

    def visit(body, prefix=""):
        for node in body.nodes:
            key = prefix + node.id
            if isinstance(node, Task):
                tasks[key] = node
            elif isinstance(node, (ForEach, Repeat)):
                visit(node.body, key + "/")

    visit(graph)
    return tasks


def graph_fingerprint(graph):
    return content_hash(graph.model_dump(mode="json", exclude_none=True))


def _source(ws, draft_id):
    source, _files = inspect_blueprint(read_archive(draft_dir(ws, draft_id) / "source.zip"))
    if source["source_hash"] != draft_id or source.get("blueprint_schema_version") != 2:
        raise FlowError("BINDING_STALE", "資格檢查需要同一版本的 Graph v2 藍圖。")
    graph, _ = validate_blueprint(source["graph"])
    return graph


def _plan(ws, graph, draft_id, selections):
    rules = graph_requirements(graph)
    tasks = _tasks(graph)
    if (
        not rules
        or not isinstance(selections, dict)
        or not selections
        or not selections.keys() <= rules.keys()
    ):
        raise FlowError("INVALID_CONFIGURATION", "請指定藍圖中各必要能力的目標 Adapter。")
    choices = {}
    for key, adapter in sorted(selections.items()):
        decision, candidates = resolve_requirement(key, tasks[key], rules[key], ws.root)
        choice = candidates.get(adapter) if isinstance(adapter, str) else None
        if not choice or not choice[0]["allowed"] or choice[1] is None:
            raise FlowError(decision["code"], "選擇的 Adapter 未觀測到或現時無權使用。")
        choices[key] = choice
    suites = graph.quality_contract.get("adapter_cases", {})
    if not isinstance(suites, dict):
        raise FlowError("INVALID_CONFIGURATION", "Adapter 案例須以工作節點或需求識別碼索引。")
    cases = {key: suites.get(key, suites.get(rules[key].id, [])) for key in choices}
    public = {
        "blueprint_hash": draft_id,
        "graph_hash": graph_fingerprint(graph),
        "adapters": {key: pair[0] for key, pair in choices.items()},
        "case_hashes": {key: content_hash(value) for key, value in cases.items()},
        "local_only": all(pair[0]["locus"] == "local_runtime" for pair in choices.values()),
    }
    if contains_private_data(public):
        raise FlowError("SECRET_IN_PORTABLE_PACKAGE", "候選描述含私人資料，資格檢查已受阻。")
    return {**public, "request_hash": content_hash(public)}, tasks, rules, choices, cases


def describe_qualification(ws, draft_id, selections):
    graph = _source(ws, draft_id)
    return _plan(ws, graph, draft_id, selections)[0]


def execute_local(binding, node, inputs, parameters):
    from .session import checked_value

    for key, value in inputs.items():
        checked_value(value, node["inputs"][key]["port"])
    if binding["provider"] == "builtin":
        value = next(iter(inputs.values()))
        if binding["tool"] == "text.upper":
            value = value.upper()
        elif binding["tool"] not in {"text.copy", "json.identity"}:
            raise FlowError("CAPABILITY_UNAVAILABLE", "缺少內建工具。")
        outputs = {next(iter(node["outputs"])): value}
    else:
        adapter = ImplementationAdapter.model_validate(binding["portable_adapter"])
        live = local_entry(adapter)
        if live is None or live[1] != binding["implementation_hash"]:
            raise FlowError("BINDING_STALE", "本機 Adapter 指紋已更改。")
        handler = live[0].load()
        outputs = handler(inputs, parameters)
    if not isinstance(outputs, dict) or outputs.keys() != node["outputs"].keys():
        raise FlowError("CONTRACT_MISMATCH", "Adapter 交付埠與合約不同。")
    for key, value in outputs.items():
        checked_value(value, node["outputs"][key])
    canonical_json(outputs)
    return outputs


def _measure(node, binding, raw_cases, limit_ms=None):
    try:
        cases = [AdapterCase.model_validate(item) for item in raw_cases]
        positives = [case for case in cases if case.expected is not None]
        if (
            not 2 <= len(cases) <= 20
            or len({case.id for case in cases}) != len(cases)
            or len({content_hash(case.inputs) for case in positives}) < 2
            or any((case.expected is None) == (case.expected_error is None) for case in cases)
            or any(case.inputs.keys() != node.inputs.keys() for case in cases)
            or any(case.expected.keys() != node.outputs.keys() for case in positives)
        ):
            raise ValueError("invalid suite")
    except (ValidationError, TypeError, ValueError):
        return ({"status": "NOT_RUN", "code": "TRIAL_CASES_REQUIRED"}, {"status": "NOT_RUN"})
    raw = canonical_json(
        {
            "binding": binding,
            "node": node.model_dump(mode="json"),
            "limit_ms": limit_ms,
            "cases": [case.model_dump(mode="json") for case in cases],
        }
    )
    # Run the same package and adapter entry-point policy in an independent interpreter.
    try:
        if len(raw) > 2 * 1024**2:
            raise ValueError("suite exceeds limit")
        result = subprocess.run(
            [sys.executable, "-I", "-m", "flow_base.graph.adapter_probe"],
            input=raw,
            capture_output=True,
            env=worker_environment(),
            timeout=25,
            check=False,
        )
        evidence = json.loads(result.stdout)
        if result.returncode or not isinstance(evidence, dict):
            raise ValueError("probe failed")
        return evidence["conformance"], evidence["behavior"]
    except subprocess.TimeoutExpired:
        return {"status": "FAIL", "code": "ADAPTER_TIMEOUT"}, {"status": "NOT_RUN"}
    except (OSError, ValueError, KeyError):
        return {"status": "NOT_RUN", "code": "ADAPTER_PROBE_UNAVAILABLE"}, {"status": "NOT_RUN"}


def _host_measure(workspace, proposal, node_id, candidate, cases):
    from .capabilities import host_catalog

    unavailable = (
        {"status": "NOT_RUN", "code": "AUTHORIZED_HOST_QUALIFICATION_REQUIRED"},
        {"status": "NOT_RUN"},
    )
    try:
        record = host_catalog(workspace).get(candidate["tool"], {})
        observed = record.get("portable_qualification")
        if (
            not isinstance(observed, dict)
            or observed.get("observation") != "executed"
            or observed.get("request_hash") != proposal["request_hash"]
            or observed.get("environment_fingerprint") != candidate["environment_fingerprint"]
            or observed.get("case_hash") != proposal["case_hashes"][node_id]
            or observed.get("model_version") != candidate["model_version"]
        ):
            return unavailable
        approval = observed.get("authorization_hash")
        if (
            not isinstance(approval, str)
            or len(approval) != 64
            or any(char not in "0123456789abcdef" for char in approval)
        ):
            return unavailable
        required = {"inputs", "outputs", "permissions", "timeout", "side_effects", "budget"}
        tests = observed.get("conformance", {})
        if (
            not isinstance(tests, dict)
            or tests.keys() != required
            or not all(result in {"PASS", "FAIL"} for result in tests.values())
        ):
            return unavailable
        expected = [AdapterCase.model_validate(case) for case in cases]
        results = observed.get("results", [])
        if (
            not 2 <= len(expected) <= 20
            or not isinstance(results, list)
            or len(results) != len(expected)
        ):
            return unavailable
        positives = [case for case in expected if case.expected is not None]
        if len({content_hash(case.inputs) for case in positives}) < 2:
            return unavailable
        behavior = []
        for case, result in zip(expected, results, strict=True):
            if (
                not isinstance(result, dict)
                or result.get("id") != case.id
                or result.get("input_hash") != content_hash(case.inputs)
                or type(result.get("cost_micro_usd")) is not int
                or not 0 <= result["cost_micro_usd"] <= candidate["cost_micro_usd"]
            ):
                return unavailable
            passed = (
                result.get("error") == case.expected_error
                if case.expected_error
                else result.get("error") is None
                and result.get("output_hash") == content_hash(case.expected)
            )
            behavior.append(
                {
                    "id": case.id,
                    "status": "PASS" if passed else "FAIL",
                    "cost_micro_usd": result["cost_micro_usd"],
                }
            )
    except (FlowError, ValueError, TypeError, AttributeError):
        return unavailable
    return (
        {
            "status": "PASS" if all(value == "PASS" for value in tests.values()) else "FAIL",
            "probes": tests,
            "approval_evidence_sha256": approval,
        },
        {
            "status": "PASS" if all(row["status"] == "PASS" for row in behavior) else "FAIL",
            "cases": behavior,
        },
    )


def qualify_bindings(ws, draft_id, selections, review):
    graph = _source(ws, draft_id)
    proposal, tasks, rules, choices, cases = _plan(ws, graph, draft_id, selections)
    if (
        not isinstance(review, BindingReview)
        or review.request_hash != proposal["request_hash"]
        or not review.evidence.strip()
    ):
        raise FlowError("USER_CONFIRMATION_REQUIRED", "請核對並確認這次資格檢查的作用範圍。")
    reports = {}
    bindings = {}
    for node_id, (candidate, transport) in choices.items():
        if candidate["locus"] == "local_runtime" and not transport["network"]:
            conformance, behavior = _measure(
                tasks[node_id], transport, cases[node_id], rules[node_id].constraints.max_latency_ms
            )
        else:
            conformance, behavior = _host_measure(
                ws.root, proposal, node_id, candidate, cases[node_id]
            )
        statuses = {conformance["status"], behavior["status"]}
        verdict = "FAIL" if "FAIL" in statuses else "NOT_RUN" if "NOT_RUN" in statuses else "PASS"
        report = {
            "graph_hash": proposal["graph_hash"],
            "blueprint_hash": draft_id,
            "node": node_id,
            "adapter": candidate["adapter"],
            "verdict": verdict,
            "environment_fingerprint": candidate["environment_fingerprint"],
            "conformance": conformance,
            "behavior": behavior,
            "case_hash": proposal["case_hashes"][node_id],
            "review_hash": content_hash([proposal["request_hash"], review.evidence]),
        }
        reports[node_id] = report
        if verdict == "PASS":
            rule = rules[node_id]
            bindings[node_id] = EnvironmentBinding(
                requirement_id=rule.id,
                capability=f"{rule.capability}@{rule.version or '1.0.0'}",
                adapter=candidate["adapter"],
                execution_locus=candidate["locus"],
                provider_version=candidate["provider_version"],
                model_version=candidate["model_version"],
                permission_scope=candidate["permission_scope"],
                schema_hash=candidate["schema_hash"],
                implementation_hash=candidate["implementation_hash"],
                environment_fingerprint=candidate["environment_fingerprint"],
                qualification_receipt=content_hash(report),
            )
    workflow = graph.workflow
    lock = None
    if bindings.keys() == rules.keys():
        confirmation = _plan(ws, graph, draft_id, selections)[0]
        if confirmation["request_hash"] != proposal["request_hash"]:
            raise FlowError("BINDING_STALE", "試做期間 Adapter、權限或環境已更新。")
        selected = EnvironmentLock(
            binding_schema_version="1.0",
            workflow=f"{workflow.id}@{workflow.version}",
            graph_hash=proposal["graph_hash"],
            blueprint_hash=draft_id,
            pattern_refs=[record["ref"] for record in graph.provenance["pattern_compositions"]],
            bindings=bindings,
        )
        lock = persist_lock(ws.root, selected, reports)
    else:
        write_receipts(ws.root, proposal["graph_hash"], reports)
    return {"status": "qualified" if lock else "blocked", "reports": reports, "lock": lock}
