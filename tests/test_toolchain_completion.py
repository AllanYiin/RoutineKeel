"""Frozen report inputs and independent expected values for toolchain acceptance."""

import json
import os
import sys
from csv import DictReader
from decimal import Decimal
from importlib.metadata import entry_points
from pathlib import Path

import pytest
from flow_base.graph.capabilities import capability_observation
from flow_base.graph.external import record_observation
from flow_base.graph.session import GraphSession
from flow_base.graph.toolchain_plan import (
    compile_toolchain,
    observed_tools,
    search_toolchains,
)
from flow_base.runtime import FlowError
from flow_base.studio import studio_request
from routinekeel_contracts.domain_assets import content_hash
from routinekeel_contracts.errors import StudioError
from routinekeel_contracts.graph_validator import validate_blueprint
from routinekeel_contracts.pattern_composition import graph_requirements
from test_binding_qualification import qualify, verify_install
from test_blueprint_completion import wait_completion
from test_capability_resolver import adapter, observed_inventory  # noqa: F401
from test_graph_builder import import_graph
from test_graph_iterations import loop_graph


def report_graph():
    case_data = Path(__file__).parent / "fixtures/toolchain_completion/report_case.json"
    case = json.loads(case_data.read_bytes())
    text = {"type": "string", "required": True}
    report = {"type": "object", "required": True}
    return {
        "blueprint_schema_version": 2,
        "workflow": {
            "id": "toolchain_report",
            "goal": "比對兩份報表的小數金額",
            "revision": 1,
            "version": "1.0.0",
        },
        "inputs": {name: text.copy() for name in case["inputs"]},
        "nodes": [
            {
                "id": "reconcile",
                "kind": "task",
                "capability": "fixture.report_reconciliation",
                "capability_version": "1.0.0",
                "permissions": [],
                "side_effect": "none",
                "inputs": {
                    name: {"source": f"input://{name}", "port": text.copy()}
                    for name in case["inputs"]
                },
                "outputs": {"report": report},
            }
        ],
        "outputs": {"report": "artifact://reconcile/report"},
        "quality_contract": {"golden_cases": [case]},
        "provenance": {},
    }


def test_pinned_report_surfaces_gap_then_resolves_observed_exact_tool(observed_inventory):  # noqa: F811
    graph = report_graph()
    node = graph["nodes"][0]
    workspace = observed_inventory()
    service, draft_id, _ = import_graph(workspace, graph)

    missing = service.resolve_blueprint_capabilities(draft_id)
    assert missing["status"] == "blocked"
    assert [(gap["node"], gap["code"]) for gap in missing["gaps"]] == [
        ("reconcile", "CAPABILITY_UNAVAILABLE")
    ]

    observed_inventory(
        {
            "tool": node["capability"],
            "version": node["capability_version"],
            "receipt_id": "fixture-observed-report-interface",
            "allowed": True,
            "available": True,
            "platforms": [sys.platform],
            "permissions": [],
            "side_effect": "none",
            "network": False,
            "inputs": {name: value["port"] for name, value in node["inputs"].items()},
            "outputs": node["outputs"],
        }
    )
    ready = service.resolve_blueprint_capabilities(draft_id)
    assert ready["status"] == "ready" and ready["gaps"] == []
    assert ready["bindings"]["reconcile"]["provider"] == "desktop_host"
    assert ready["fingerprint"] != missing["fingerprint"]


def test_abstract_report_keeps_rejections_and_resumes_the_same_completion(observed_inventory):  # noqa: F811
    graph = report_graph()
    graph["graph_contract_version"] = "2.3"
    node = graph["nodes"][0]
    node["capability_requirement"] = {
        "id": "compare_reports",
        "capability": node["capability"],
        "version_range": ">=1.0.0,<2.0.0",
        "required": True,
        "portability": "flexible",
        "output_semantics": {"report": "fixture.report_diff"},
    }

    def observed_tool(tool, *, version="1.1.0", semantic="fixture.report_diff", allowed=True):
        observation = adapter(tool, cap=node["capability"], allowed=allowed)
        observation["inputs"] = {name: value["port"] for name, value in node["inputs"].items()}
        observation["outputs"] = node["outputs"]
        observation["portable"].update(
            capability_version=version,
            inputs=observation["inputs"],
            outputs={"report": {**node["outputs"]["report"], "schema_id": semantic}},
        )
        return observation

    wrong_version = observed_tool("report.newer", version="2.0.0")
    wrong_semantics = observed_tool("report.summary", semantic="fixture.summary")
    denied = observed_tool("report.exact", allowed=False)
    workspace = observed_inventory(wrong_version, wrong_semantics, denied)
    service, draft_id, _ = import_graph(workspace, graph)
    started = service.blueprint_completion(
        "start", payload={"draft_id": draft_id, "request_id": "start"}
    )
    (gap,) = started["gaps"]
    assert gap["requirement_id"] == "compare_reports"
    assert {(row["tool"], row["code"], row["field"]) for row in gap["candidate_rejections"]} == {
        ("report.newer", "VERSION_MISMATCH", "version"),
        ("report.summary", "PORT_SEMANTICS_MISMATCH", "outputs/report"),
        ("report.exact", "PERMISSION_DENIED", "authorization"),
    }
    assert gap["observation"]["status"] == "current"
    observed_inventory(denied, expired=True)
    expired = service.blueprint_completion(
        "recheck",
        started["work_id"],
        {"request_id": "expired", "expected_revision": started["revision"]},
    )
    assert expired["capability_observation"]["status"] == "expired"
    assert expired["gaps"][0]["candidates"] == []

    observed_inventory(observed_tool("report.exact"))
    renewed = service.blueprint_completion(
        "recheck",
        started["work_id"],
        {"request_id": "renewed", "expected_revision": expired["revision"]},
    )
    assert renewed["status"] == "needs_input" and renewed["work_id"] == started["work_id"]
    assert renewed["capability_observation"]["id"] != gap["observation"]["id"]
    (candidate,) = renewed["gaps"][0]["candidates"]
    assert candidate["tool"] == "report.exact" and candidate["capability_version"] == "1.1.0"
    assert candidate["behavioral_verdict"] == "NOT_RUN"


def test_nested_general_requirement_qualifies_without_pattern_admission(tmp_path):
    graph = loop_graph()
    graph["graph_contract_version"] = "2.3"
    graph["nodes"][0]["body"]["nodes"][0]["capability_requirement"] = {
        "id": "uppercase",
        "capability": "text.upper",
        "version": "1.0.0",
        "required": True,
        "portability": "flexible",
        "execution_loci": ["local_runtime"],
    }
    graph["quality_contract"]["adapter_cases"] = {
        "loop/work": [
            {"id": "mixed", "inputs": {"source": "Mixed"}, "expected": {"value": "MIXED"}},
            {"id": "unicode", "inputs": {"source": "é"}, "expected": {"value": "É"}},
        ]
    }
    service, key, _ = import_graph(tmp_path, graph)
    initial = service.resolve_blueprint_capabilities(key)
    assert initial["gaps"][0]["node"] == "loop/work"
    qualified = qualify(service, key, {"loop/work": "routinekeel.text_upper@1.0.0"})
    assert qualified["status"] == "qualified" and qualified["lock"]["pattern_refs"] == []
    verify_install(service, key)
    request = tmp_path / "rows.json"
    request.write_text('{"inputs":{"rows":["a","b"]}}', encoding="utf-8")
    result = service.run_workflow_sync({"task": graph["workflow"]["id"], "source": request})
    assert result["blueprint"]["outputs"] == {"answer": ["A", "B"]}


def test_explicit_requirements_are_versioned_and_share_pattern_paths():
    from routinekeel_contracts.domain_assets import content_hash
    from routinekeel_contracts.graph import Task
    from test_binding_qualification import echo_blueprint

    graph = echo_blueprint()
    node = graph["nodes"][0]
    (composition,) = graph["provenance"]["pattern_compositions"]
    pattern = composition["requirements"][node["id"]]
    node["capability_requirement"] = dict(pattern)

    def refresh_member():
        model = Task.model_validate(node)
        composition["members"][node["id"]] = content_hash(
            model.model_dump(mode="json", exclude_none=True)
        )

    refresh_member()
    with pytest.raises(StudioError, match="Graph 2.3"):
        validate_blueprint(graph)
    graph["graph_contract_version"] = "2.3"
    parsed, _ = validate_blueprint(graph)
    assert list(graph_requirements(parsed)) == [node["id"]]

    node["capability_requirement"]["version_range"] = ">=1.0.0,<2.0.0"
    node["capability_requirement"].pop("version")
    refresh_member()
    with pytest.raises(StudioError, match="Pattern 能力需求衝突"):
        validate_blueprint(graph)


def test_studio_review_shows_versioned_requirement_before_qualification(tmp_path):
    graph = report_graph()
    graph["graph_contract_version"] = "2.3"
    node = graph["nodes"][0]
    node["capability_requirement"] = {
        "id": "compare_reports",
        "capability": node["capability"],
        "version_range": ">=1.0.0,<2.0.0",
        "required": True,
        "portability": "flexible",
        "output_semantics": {"report": "fixture.report_diff"},
    }
    project = studio_request(
        tmp_path, "create", {"name": "報表驗收", "task": graph["workflow"]["goal"]}
    )
    draft = studio_request(
        tmp_path,
        "prepare_blueprint",
        {
            "project_id": project["project_id"],
            "expected_revision": project["revision"],
            "request_id": "report-draft",
            "graph": graph,
        },
    )
    visible = draft["draft_design"]["tools"]["reconcile"]["requirement"]
    assert visible["version_range"] == ">=1.0.0,<2.0.0"
    assert visible["output_semantics"] == {"report": "fixture.report_diff"}
    assert "compare_reports" in draft["review_sections"]["implementation"]["display_text"]
    assert draft["build_preflight"]["capability_gaps"][0]["node"] == "reconcile"
    assert not draft["build_preflight"]["blocks_blueprint_export"]


def test_report_planner_inserts_conversion_and_accounts_for_cost(observed_inventory, monkeypatch):  # noqa: F811
    graph = report_graph()
    graph["graph_contract_version"] = "2.3"
    node = graph["nodes"][0]
    node["capability_requirement"] = {
        "id": "compare_reports",
        "capability": node["capability"],
        "version": "1.0.0",
        "required": True,
        "portability": "flexible",
        "input_semantics": {"left": "fixture.left_csv", "right": "fixture.right_csv"},
        "output_semantics": {"report": "fixture.report_diff"},
        "constraints": {"max_cost_micro_usd": 6, "max_latency_ms": 60},
    }

    def tool(name, inputs, outputs, cost):
        entry = adapter(name, cap=name)
        entry["resources"]["cost_micro_usd"] = cost
        entry["resources"]["model_calls"] = 0
        descriptor = entry["portable"]
        descriptor.update(max_cost_micro_usd=cost, max_latency_ms=20)
        for field, ports in (("inputs", inputs), ("outputs", outputs)):
            entry[field] = {
                port: {"type": type_, "required": True} for port, (type_, _schema) in ports.items()
            }
            descriptor[field] = {
                port: {**entry[field][port], "schema_id": schema}
                for port, (_type, schema) in ports.items()
            }
        return entry

    reader = tool(
        "report.read",
        {
            "left": ("string", "fixture.left_csv"),
            "right": ("string", "fixture.right_csv"),
        },
        {"tables": ("object", "fixture.raw_tables")},
        2,
    )
    convert = tool(
        "report.normalize",
        {"tables": ("object", "fixture.raw_tables")},
        {
            "ready": ("object", "fixture.decimal_tables"),
        },
        1,
    )
    compare = tool(
        "report.compare",
        {"tables": ("object", "fixture.decimal_tables")},
        {
            "report": ("object", "fixture.report_diff"),
        },
        3,
    )
    wrong = tool(
        "report.spurious",
        {"tables": ("object", "fixture.raw_tables")},
        {
            "report": ("object", "fixture.summary"),
        },
        0,
    )
    workspace = observed_inventory(compare, wrong, convert, reader)
    service, draft_id, _ = import_graph(workspace, graph)
    status = service.blueprint_completion(
        "start", payload={"draft_id": draft_id, "request_id": "plan"}
    )
    (gap,) = status["gaps"]
    (candidate,) = gap["candidates"]
    assert candidate["locus"] == "host_mediated" and status["status"] == "needs_input"
    assert candidate["behavioral_verdict"] == "NOT_RUN"
    result = gap["toolchain_planning"]
    (plan,) = result["plans"]
    assert result["status"] == "proposed" and plan["qualification"] == "NOT_RUN"
    assert len(plan["compiled_graph_hash"]) == 64
    assert plan["cost_micro_usd"] == 6
    assert [step["tool"] for step in plan["steps"]] == [
        "report.read",
        "report.normalize",
        "report.compare",
    ]
    first, middle, last = plan["steps"]
    assert first["inputs"] == {"left": "input://left", "right": "input://right"}
    assert middle["inputs"] == {"tables": f"artifact://{first['id']}/tables"}
    assert last["inputs"] == {"tables": f"artifact://{middle['id']}/ready"}
    assert plan["outputs"] == {"report": f"artifact://{last['id']}/report"}
    fresh = service.resolve_blueprint_capabilities(draft_id)["toolchain_planning"]["reconcile"]
    assert fresh == {key: value for key, value in result.items() if key != "search_id"}

    inventory, _ = capability_observation(workspace)
    operations = list(observed_tools(workspace, inventory))
    parsed, _ = validate_blueprint(graph)
    task = parsed.nodes[0]
    rule = task.capability_requirement
    limited = search_toolchains(task, rule, operations, maximum_states=1)
    assert limited == {"status": "search_limit_reached", "plans": [], "evaluated": 1}
    low_budget = rule.model_copy(
        update={"constraints": rule.constraints.model_copy(update={"max_cost_micro_usd": 5})}
    )
    assert search_toolchains(task, low_budget, operations)["plans"] == []

    def read(arguments):
        return {
            "tables": {
                side: list(DictReader(text.splitlines())) for side, text in arguments.items()
            }
        }

    def normalize(arguments):
        return {
            "ready": {
                side: {
                    row["id"]: str(Decimal(row["amount"]).quantize(Decimal("0.01"))) for row in rows
                }
                for side, rows in arguments["tables"].items()
            }
        }

    def reconcile(arguments):
        left = arguments["tables"]["left"]
        right = arguments["tables"]["right"]
        shared = sorted(left.keys() & right.keys())
        return {
            "report": {
                "matched": [key for key in shared if left[key] == right[key]],
                "differences": [
                    {"id": key, "right_minus_left": str(Decimal(right[key]) - Decimal(left[key]))}
                    for key in shared
                    if left[key] != right[key]
                ],
                "right_only": sorted(right.keys() - left.keys()),
            }
        }

    handlers = {"report.read": read, "report.normalize": normalize, "report.compare": reconcile}
    compiled, bindings = compile_toolchain(
        task, rule, plan, operations, graph["workflow"], workspace
    )
    assert compiled["graph_hash"] == plan["compiled_graph_hash"]
    (case,) = graph["quality_contract"]["golden_cases"]
    runtime = service.workspace.runtime
    view = GraphSession.begin(runtime, compiled, {"inputs": case["inputs"]}, workspace, bindings)
    session = GraphSession(runtime, view["session_id"])
    for index, operation in enumerate(handlers):
        pending = view["pending"]
        assert pending["scope"]["tool"] == operation and pending["kind"] == "authorize_external"
        view = session.interact(
            "authorize",
            {
                "task_id": pending["id"],
                "revision": view["revision"],
                "scope_hash": pending["scope_hash"],
                "payload_hash": pending["payload_hash"],
                "user_text": "同意本機測具試跑",
                "request_id": f"authorize-{index}",
            },
        )
        pending = view["pending"]
        view = session.interact(
            "claim",
            {
                "task_id": pending["id"],
                "revision": view["revision"],
                "request_id": f"claim-{index}",
            },
        )
        dispatched = view["dispatch"]
        assert dispatched["tool"] == operation
        output = handlers[operation](dispatched["arguments"])
        record_observation(
            workspace,
            dispatched["idempotency_key"],
            dispatched["scope_hash"],
            f"fixture-observed-{index}",
            "succeeded",
            output,
            usage={"cost_micro_usd": 0, "model_calls": 0},
        )
        view = session.interact(
            "reconcile",
            {
                "task_id": pending["id"],
                "revision": view["revision"],
                "request_id": f"reconcile-{index}",
            },
        )
    assert view["status"] == "complete" and view["outputs"] == case["expected"]

    monkeypatch.setattr("flow_base.graph.toolchain_search.SEARCH_SLICE", 1)
    state = service.blueprint_completion(
        "start",
        payload={"draft_id": draft_id, "request_id": "limited-search"},
    )
    first_tick = state["gaps"][0]["toolchain_planning"]
    assert first_tick["status"] == "search_limit_reached" and first_tick["evaluated"] == 1
    assert "checkpoint" not in first_tick
    assert state["toolchain_searches"] == {"reconcile": first_tick["search_id"]}
    limited_gap = state["gaps"][0]
    evidence = {
        "action": "propose_toolchain",
        "gap_id": limited_gap["id"],
        "scope_hash": limited_gap["scope_hash"],
        "expected_revision": state["revision"],
    }
    suggestion = {
        "steps": [
            {"id": step["id"], "tool": step["tool"], "inputs": step["inputs"]}
            for step in plan["steps"]
        ],
        "outputs": plan["outputs"],
    }
    bogus = {
        **suggestion,
        "steps": [
            {**suggestion["steps"][0], "tool": "report.not_observed"},
            *suggestion["steps"][1:],
        ],
    }
    with pytest.raises(FlowError, match="提案工具未觀測到"):
        service.blueprint_completion(
            "submit",
            state["work_id"],
            {
                **evidence,
                "request_id": "bogus-suggestion",
                "proposal": bogus,
            },
        )
    state = service.blueprint_completion(
        "submit",
        state["work_id"],
        {
            **evidence,
            "request_id": "host-suggestion",
            "proposal": suggestion,
        },
    )
    assert state["gaps"][0]["toolchain_planning"]["plans"] == [plan]
    assert state["gaps"][0]["toolchain_planning"]["evaluated"] == 1
    for tick in range(2, 40):
        from flow_base.core.service import WorkflowService

        reopened = WorkflowService(workspace)
        state = reopened.blueprint_completion(
            "advance",
            state["work_id"],
            {
                "request_id": f"tick-{tick}",
                "expected_revision": state["revision"],
            },
        )
        resumed = state["gaps"][0]["toolchain_planning"]
        if resumed["status"] == "proposed":
            break
        assert resumed["evaluated"] == tick
    assert resumed["status"] == "proposed" and resumed["plans"] == [plan]
    observed_inventory(reader, convert, compare)
    refreshed = reopened.blueprint_completion(
        "recheck",
        state["work_id"],
        {
            "request_id": "changed-observation",
            "expected_revision": state["revision"],
        },
    )
    new_tick = refreshed["gaps"][0]["toolchain_planning"]
    assert new_tick["evaluated"] == 1 and new_tick["search_id"] != first_tick["search_id"]


def test_local_toolchain_qualifies_builds_installs_and_runs(observed_inventory):  # noqa: F811
    tools = {entry.name for entry in entry_points(group="routinekeel.portable_adapters")}
    if not {"routinekeel.csv_read", "routinekeel.decimal_reconcile"} <= tools:
        if os.environ.get("ROUTINEKEEL_E2E_ENFORCE") == "1":
            pytest.fail("The Data Adapter wheel is required for the toolchain candidate run")
        pytest.skip("The optional Data Adapter wheel is not installed")

    csv_port, rows_port, status_port = {"type": "string"}, {"type": "array"}, {"type": "string"}
    reader = adapter("routinekeel.csv_read", cap="tabular.read", locus="local_runtime")
    compare = adapter(
        "routinekeel.decimal_reconcile", cap="tabular.reconcile", locus="local_runtime"
    )
    reader.update(inputs={"source": csv_port}, outputs={"rows": rows_port})
    reader["portable"].update(
        inputs={"source": {**csv_port, "schema_id": "report.left_csv"}},
        outputs={"rows": {**rows_port, "schema_id": "report.left_rows"}},
    )
    compare.update(inputs={"left": rows_port, "right": rows_port}, outputs={"status": status_port})
    compare["portable"].update(
        inputs={
            "left": {**rows_port, "schema_id": "report.left_rows"},
            "right": {**rows_port, "schema_id": "report.right_rows"},
        },
        outputs={"status": {**status_port, "schema_id": "report.reconcile_status"}},
    )
    workspace = observed_inventory(reader, compare)
    graph = report_graph()
    graph["graph_contract_version"] = "2.3"
    graph["inputs"] = {"left": csv_port, "right": rows_port}
    graph["outputs"] = {"status": "artifact://reconcile/status"}
    node = graph["nodes"][0]
    node["inputs"] = {
        key: {"source": "input://" + key, "port": port} for key, port in graph["inputs"].items()
    }
    node["outputs"] = {"status": status_port}
    node["capability_requirement"] = {
        "id": "reconcile_tables",
        "capability": node["capability"],
        "version": "1.0.0",
        "required": True,
        "portability": "flexible",
        "input_semantics": {"left": "report.left_csv", "right": "report.right_rows"},
        "output_semantics": {"status": "report.reconcile_status"},
    }
    inputs = {"left": "id,amount\na,1.20\n", "right": [{"id": "a", "amount": "1.20"}]}
    cases = [
        {"inputs": inputs, "expected": {"status": "matched"}},
        {
            "inputs": {**inputs, "right": [{"id": "a", "amount": "1.30"}]},
            "expected": {"status": "mismatched"},
        },
    ]
    graph["quality_contract"] = {
        "golden_cases": cases,
        "adapter_cases": {
            "reconcile": [{"id": f"case_{n}", **case} for n, case in enumerate(cases)]
        },
    }
    service, draft_id, _ = import_graph(workspace, graph)
    started = service.blueprint_completion(
        "start",
        payload={
            "draft_id": draft_id,
            "request_id": "install-chain",
            "intent": "install",
            "offline": True,
        },
    )
    final = wait_completion(service, started, ("installed", "needs_input"))
    assert final["status"] == "installed", final
    receipt = final["qualification"]["reconcile"]["report"]
    assert receipt["verdict"] == "PASS" and len(receipt["toolchain_graph_hash"]) == 64
    assert final["candidate"]["status"] == "verified"
    assert [
        case["simulated_host_and_review"] for case in final["candidate"]["evidence"]["cases"]
    ] == [[], []]
    request = workspace / "chain-request.json"
    request.write_text(json.dumps({"inputs": inputs}), encoding="utf-8")
    report = service.run_workflow_sync(
        {"task": "toolchain_report", "source": request, "offline": True}
    )
    assert report["blueprint"]["outputs"] == {"status": "matched"}
    observed_inventory(reader, {**compare, "allowed": False})
    expired = service.resolve_blueprint_capabilities(draft_id, offline=True)
    assert expired["status"] == "blocked" and expired["gaps"][0]["code"] == "BINDING_STALE"


def test_host_toolchain_replays_candidate_and_dispatches_children(observed_inventory):  # noqa: F811
    from flow_base.graph.builder import preflight_graph

    string = {"type": "string", "required": True}

    def hosted(name, source, target):
        row = adapter(name, cap=name)
        row.update(inputs={"text": string}, outputs={"text": string})
        row["portable"].update(
            inputs={"text": {**string, "schema_id": source}},
            outputs={"text": {**string, "schema_id": target}},
        )
        return row

    upper = hosted("test.uppercase", "test.raw_text", "test.upper_text")
    label = hosted("test.label_text", "test.upper_text", "test.labelled_text")
    workspace = observed_inventory(upper, label)
    graph = report_graph()
    graph.update(
        graph_contract_version="2.3",
        inputs={"text": string},
        outputs={"text": "artifact://reconcile/text"},
    )
    node = graph["nodes"][0]
    node.update(
        inputs={"text": {"source": "input://text", "port": string}},
        outputs={"text": string},
        capability_requirement={
            "id": "format_text",
            "capability": node["capability"],
            "version": "1.0.0",
            "required": True,
            "portability": "flexible",
            "input_semantics": {"text": "test.raw_text"},
            "output_semantics": {"text": "test.labelled_text"},
        },
    )
    (plan,) = preflight_graph(graph, workspace)["toolchain_planning"]["reconcile"]["plans"]
    handlers = {"test.uppercase": str.upper, "test.label_text": lambda value: "[" + value + "]"}
    cases, observations = [], []
    for index, (text, expected) in enumerate([("first", "[FIRST]"), ("second", "[SECOND]")]):
        inputs = {"text": text}
        case = {"inputs": inputs, "expected": {"text": expected}}
        observation = {
            "id": f"case_{index}",
            "input_hash": content_hash(inputs),
            "cost_micro_usd": 0,
            "steps": [],
        }
        value, replies = text, []
        for step in plan["steps"]:
            value = handlers[step["tool"]](value)
            replies.append(
                {
                    "step": "reconcile",
                    "child_step": step["id"],
                    "kind": "host",
                    "outputs": {"text": value},
                }
            )
            observation["steps"].append(
                {
                    "step_id": step["id"],
                    "binding_fingerprint": step["binding_fingerprint"],
                    "provider_receipt": f"local-test-observer-{index}-{step['id']}",
                    "cost_micro_usd": 0,
                }
            )
        observation["output_hash"] = content_hash({"text": value})
        observations.append(observation)
        cases.append({**case, "replies": replies})
    graph["quality_contract"] = {
        "golden_cases": cases,
        "adapter_cases": {
            "reconcile": [
                {"id": f"case_{n}", "inputs": case["inputs"], "expected": case["expected"]}
                for n, case in enumerate(cases)
            ]
        },
    }
    service, draft_id, _ = import_graph(workspace, graph)
    started = service.blueprint_completion(
        "start", payload={"draft_id": draft_id, "request_id": "host-chain", "intent": "install"}
    )
    (gap,) = started["gaps"]
    (candidate,) = gap["candidates"]
    selected = service.blueprint_completion(
        "submit",
        started["work_id"],
        {
            "request_id": "select-host-chain",
            "expected_revision": started["revision"],
            "gap_id": gap["id"],
            "scope_hash": gap["scope_hash"],
            "action": "select_adapter",
            "adapter": candidate["adapter"],
            "user_text": "使用這個已展示的宿主工具鏈測具",
        },
    )
    scope = selected["qualification"]["reconcile"]["scope"]
    assert selected["qualification"]["reconcile"]["report"]["verdict"] == "NOT_RUN"
    proof = {
        "observation": "executed",
        "request_hash": scope["request_hash"],
        "environment_fingerprint": candidate["environment_fingerprint"],
        "case_hash": scope["case_hashes"]["reconcile"],
        "toolchain_graph_hash": plan["compiled_graph_hash"],
        "authorization_hash": "f" * 64,
        "conformance": {
            key: "PASS"
            for key in ("inputs", "outputs", "permissions", "timeout", "side_effects", "budget")
        },
        "results": observations,
    }
    observed_inventory(upper, label, {"tool": candidate["tool"], "portable_qualification": proof})
    queued = service.blueprint_completion(
        "recheck",
        started["work_id"],
        {
            "request_id": "host-observations",
            "expected_revision": selected["revision"],
        },
    )
    final = wait_completion(service, queued, ("installed", "needs_input"))
    assert final["status"] == "installed", final.get("gaps")
    assert final["qualification"]["reconcile"]["report"]["verdict"] == "PASS"
    assert final["candidate"]["evidence"]["provider_calls"] == 0
    assert all(
        len(case["simulated_host_and_review"]) == 2
        for case in final["candidate"]["evidence"]["cases"]
    )
    request = workspace / "deployed-chain.json"
    request.write_text('{"inputs":{"text":"third"}}', encoding="utf-8")
    view = service.run_workflow_sync({"task": "toolchain_report", "source": request})["blueprint"]
    session = GraphSession(service.workspace.runtime, view["session_id"])
    for expected_tool, handler in handlers.items():
        pending = view["pending"]
        claimed = session.interact(
            "claim", {"task_id": pending["id"], "revision": view["revision"]}
        )
        dispatch = claimed["dispatch"]
        assert dispatch["tool"] == expected_tool
        record_observation(
            workspace,
            dispatch["idempotency_key"],
            dispatch["scope_hash"],
            "local-deployed-test",
            "succeeded",
            {"text": handler(dispatch["arguments"]["text"])},
        )
        view = session.interact(
            "reconcile", {"task_id": pending["id"], "revision": claimed["revision"]}
        )
    assert view["outputs"] == {"text": "[THIRD]"}

    request = workspace / "rebind-chain.json"
    request.write_text('{"inputs":{"text":"fourth"}}', encoding="utf-8")
    interrupted = service.run_workflow_sync({"task": "toolchain_report", "source": request})
    source_run_id = interrupted["run_id"]
    waiting = interrupted["blueprint"]
    session_id = waiting["session_id"]
    old_pending = waiting["pending"]
    observed_inventory(
        upper,
        {**label, "allowed": False},
        {"tool": candidate["tool"], "portable_qualification": proof},
    )
    rebinding = service.blueprint_task(
        session_id,
        "claim",
        {
            "task_id": old_pending["id"],
            "revision": waiting["revision"],
            "request_id": "hold-changed-chain",
        },
    )
    assert rebinding["code"] == "REBIND_REQUIRED" and "dispatch" not in rebinding
    assert rebinding["root_run_id"] == rebinding["active_run_id"] == source_run_id
    work = service.blueprint_completion("status", rebinding["rebind_work_id"])
    assert work["origin"]["source_run_id"] == source_run_id and work["status"] == "needs_input"
    again = service.blueprint_task(session_id, "next")
    assert (
        again["rebind_work_id"] == work["work_id"] and again["pending"]["id"] == old_pending["id"]
    )

    observed_inventory(upper, label, {"tool": candidate["tool"], "portable_qualification": proof})
    restored = service.blueprint_task(
        session_id,
        "claim",
        {
            "task_id": old_pending["id"],
            "revision": again["revision"],
            "request_id": "restored-chain",
        },
    )
    assert restored["session_id"] == session_id and restored["dispatch"]["tool"] == "test.uppercase"
    assert "rebind_work_id" not in restored
