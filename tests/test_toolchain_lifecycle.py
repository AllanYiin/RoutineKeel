"""Reconcile two CSVs across preparation, installation and runtime replacement."""

import json
import sys
from contextlib import closing
from pathlib import Path

from flow_base.blueprints.wheel import write_wheel
from flow_base.graph.session import GraphSession
from flow_base.runtime import read_json, sha256
from test_blueprint_completion import wait_completion
from test_capability_resolver import adapter, observed_inventory  # noqa: F401
from test_graph_builder import import_graph
from test_toolchain_completion import report_graph
from test_workspace import cli

FIXTURES = Path(__file__).parent / "fixtures" / "toolchain_completion"
DISTRIBUTION = "rk_fixture_report_tools"
VERSION = "1.0.0"
TEXT = {"type": "string", "required": True}
OBJECT = {"type": "object", "required": True}
CSV = {name: {**TEXT, "schema_id": f"fixture.{name}_csv"} for name in ("left", "right")}
PAIR = {"pair": {**OBJECT, "schema_id": "fixture.raw_pair"}}
NORMALIZED = {"normalized": {**OBJECT, "schema_id": "fixture.normalized_pair"}}
REPORT = {"report": {**OBJECT, "schema_id": "fixture.report_diff"}}


def _inventory(host):
    entries = [
        ("read_pair", "fixture.pair_reader", CSV, PAIR),
        ("normalize", "fixture.amount_normalizer", PAIR, NORMALIZED),
        ("compare", "fixture.report_compare", NORMALIZED, REPORT),
        ("normalize_replacement", "fixture.amount_normalizer", PAIR, NORMALIZED),
    ]
    wheel = host / f"{DISTRIBUTION}-{VERSION}-py3-none-any.whl"
    entry_points = "[routinekeel.portable_adapters]\n" + "".join(
        f"fixture.{name}={DISTRIBUTION}:{name}\n" for name, *_ in entries
    )
    write_wheel(
        wheel,
        DISTRIBUTION,
        VERSION,
        {
            f"{DISTRIBUTION}/__init__.py": (FIXTURES / "report_tools.py").read_bytes(),
            f"{DISTRIBUTION}-{VERSION}.dist-info/entry_points.txt": entry_points.encode(),
        },
        dependencies=(),
    )
    artifact = {
        "artifact": wheel.name,
        "sha256": sha256(wheel),
        "distribution": DISTRIBUTION.replace("_", "-"),
        "version": VERSION,
    }
    records = []
    for name, capability, inputs, outputs in entries:
        record = adapter(f"fixture.{name}", cap=capability, locus="local_runtime")
        record.update(
            inputs={key: _graph_port(port) for key, port in inputs.items()},
            outputs={key: _graph_port(port) for key, port in outputs.items()},
            preparation_allowed=True,
            preparation={
                "recipe_schema_version": 1,
                "id": f"fixture.prepare_{name}",
                "version": VERSION,
                "action": "install_locked_wheels",
                "tool": record["tool"],
                "distribution": artifact["distribution"],
                "platforms": [sys.platform],
                "wheels": [artifact],
            },
        )
        record["portable"].update(
            distribution=artifact["distribution"], inputs=inputs, outputs=outputs
        )
        records.append(record)
    return records


def _task(name, capability, inputs, outputs):
    return {
        "id": name,
        "kind": "task",
        "capability": capability,
        "capability_version": VERSION,
        "permissions": [],
        "side_effect": "none",
        "inputs": {
            key: {**value, "port": _graph_port(value["port"])} for key, value in inputs.items()
        },
        "outputs": {key: _graph_port(port) for key, port in outputs.items()},
        "capability_requirement": {
            "id": name,
            "capability": capability,
            "version": VERSION,
            "required": True,
            "portability": "flexible",
            "input_semantics": {key: port["port"]["schema_id"] for key, port in inputs.items()},
            "output_semantics": {key: port["schema_id"] for key, port in outputs.items()},
        },
    }


def _graph_port(port):
    return {key: value for key, value in port.items() if key != "schema_id"}


def _blueprint():
    graph = report_graph()
    first = graph["quality_contract"]["golden_cases"][0]
    second = {
        "inputs": {"left": "id,amount\nA,2.20\n", "right": "id,amount\nA,2.10\n"},
        "expected": {
            "report": {
                "matched": [],
                "differences": [{"id": "A", "right_minus_left": "-0.10"}],
                "right_only": [],
            }
        },
    }
    pairs = [
        {
            "left": [{"id": "K01", "amount": "0.30"}, {"id": "K02", "amount": "1.20"}],
            "right": [
                {"id": "K01", "amount": "0.30"},
                {"id": "K02", "amount": "1.30"},
                {"id": "K03", "amount": "2.00"},
            ],
        },
        {
            "left": [{"id": "A", "amount": "2.20"}],
            "right": [{"id": "A", "amount": "2.10"}],
        },
    ]
    read = _task(
        "read",
        "fixture.pair_reader",
        {name: {"source": f"input://{name}", "port": port} for name, port in CSV.items()},
        PAIR,
    )
    reconcile = _task(
        "reconcile",
        "fixture.report_reconciliation",
        {"pair": {"source": "artifact://review/pair", "port": PAIR["pair"]}},
        REPORT,
    )
    graph.update(
        graph_contract_version="2.3",
        inputs={key: _graph_port(port) for key, port in CSV.items()},
        nodes=[
            read,
            {
                "id": "review",
                "kind": "human_review",
                "prompt": "請確認已讀取兩份報表。",
                "inputs": {"pair": {"source": "artifact://read/pair", "port": OBJECT}},
                "outputs": {"pair": OBJECT},
            },
            reconcile,
        ],
    )
    cases = list(zip((first, second), pairs, strict=True))
    graph["quality_contract"] = {
        "golden_cases": [
            {
                **case,
                "replies": [{"step": "review", "kind": "human", "outputs": {"pair": pair}}],
            }
            for case, pair in cases
        ],
        "adapter_cases": {
            "read": [
                {
                    "id": f"read_{n}",
                    "inputs": case["inputs"],
                    "expected": {"pair": pair},
                }
                for n, (case, pair) in enumerate(cases)
            ],
            "reconcile": [
                {
                    "id": f"report_{n}",
                    "inputs": {"pair": pair},
                    "expected": case["expected"],
                }
                for n, (case, pair) in enumerate(cases)
            ],
        },
    }
    return graph, first, pairs[0]


def _chain_tools(session):
    binding = session["bindings"]["reconcile"]
    return [binding["chain_bindings"][step]["tool"] for step in binding["chain_plan"]["order"]]


def test_plans_and_runs_one_three_step_adapter_chain(observed_inventory):  # noqa: F811
    workspace = observed_inventory()
    reader, normalize, compare, _replacement = _inventory(workspace.parent / "host")
    observed_inventory(reader, normalize, compare)
    graph, example, _pair = _blueprint()
    cases = [
        {"inputs": case["inputs"], "expected": case["expected"]}
        for case in graph["quality_contract"]["golden_cases"]
    ]
    graph["nodes"] = [
        _task(
            "reconcile",
            "fixture.report_reconciliation",
            {key: {"source": f"input://{key}", "port": port} for key, port in CSV.items()},
            REPORT,
        )
    ]
    graph["quality_contract"] = {
        "golden_cases": cases,
        "adapter_cases": {
            "reconcile": [{"id": f"whole_{index}", **case} for index, case in enumerate(cases)]
        },
    }
    service, draft, _ = import_graph(workspace, graph)
    missing = service.resolve_blueprint_capabilities(draft, offline=True)
    assert missing["status"] == "blocked"
    assert {
        row["tool"]
        for row in missing["portable_decisions"][0]["rejections"]
        if row["code"] == "DEPENDENCY_NOT_INSTALLED"
    } == {reader["tool"], normalize["tool"], compare["tool"]}
    started = service.blueprint_completion(
        "start",
        payload={
            "draft_id": draft,
            "intent": "install",
            "request_id": "a2-install",
            "offline": True,
        },
    )
    installed = wait_completion(service, started, ("installed", "needs_input"))
    assert installed["status"] == "installed", installed.get("gaps")
    assert all(
        not case["simulated_host_and_review"]
        for case in installed["candidate"]["evidence"]["cases"]
    )
    source = workspace / "report.json"
    source.write_text(json.dumps({"inputs": example["inputs"]}), encoding="utf-8")
    result = service.run_workflow_sync(
        {"task": "toolchain_report", "source": source, "offline": True}
    )
    state = read_json(
        GraphSession(service.workspace.runtime, result["blueprint"]["session_id"]).path
    )
    assert _chain_tools(state) == [reader["tool"], normalize["tool"], compare["tool"]]
    assert result["blueprint"]["outputs"] == example["expected"]


def test_installed_three_tool_workflow_resumes_after_normalizer_drift(
    observed_inventory,  # noqa: F811
):
    workspace = observed_inventory()
    reader, old, compare, replacement = _inventory(workspace.parent / "host")
    observed_inventory(reader, old, compare)
    graph, example, pair = _blueprint()
    service, draft, _ = import_graph(workspace, graph)
    assert service.resolve_blueprint_capabilities(draft, offline=True)["status"] == "blocked"
    started = service.blueprint_completion(
        "start",
        payload={
            "draft_id": draft,
            "intent": "install",
            "request_id": "a9-install",
            "offline": True,
        },
    )
    installed = wait_completion(service, started, ("installed", "needs_input"))
    assert installed["status"] == "installed", installed.get("gaps")
    assert {
        item["entry_point"]
        for item in installed["preparations"]["reconcile"]["verified_operations"].values()
    } == {
        f"{DISTRIBUTION}:normalize",
        f"{DISTRIBUTION}:compare",
    }
    assert installed["candidate"]["status"] == "verified"

    source = workspace / "report-request.json"
    source.write_text(json.dumps({"inputs": example["inputs"]}), encoding="utf-8")
    run = service.run_workflow_sync({"task": "toolchain_report", "source": source, "offline": True})
    original_run = run["run_id"]
    runtime = service.workspace.runtime
    session_id = "job_" + original_run.removeprefix("run_")
    path = GraphSession(runtime, session_id).path
    state = read_json(path)
    assert _chain_tools(state) == [old["tool"], compare["tool"]]
    assert state["nodes"]["read"]["outputs"] == {"pair": pair}
    read_hash = state["nodes"]["read"]["outputs_hash"]
    review = service.blueprint_task(session_id, "status")
    assert review["pending"]["step"] == "review"

    observed_inventory(reader, {**old, "allowed": False}, compare, replacement)
    waiting = service.blueprint_task(
        session_id,
        "complete",
        {
            "revision": review["revision"],
            "task_id": review["pending"]["id"],
            "outputs": {"pair": pair},
            "evidence": {"user_text": "確認兩份報表"},
        },
    )
    assert waiting["code"] == "REBIND_REQUIRED"
    repair = wait_completion(
        service,
        service.blueprint_completion("status", waiting["rebind_work_id"]),
        ("verified", "needs_input"),
    )
    assert repair["status"] == "verified", repair.get("gaps")
    assert repair["candidate"]["status"] == "verified"
    assert repair["candidate"]["build_id"] != installed["candidate"]["build_id"]
    scope = cli(workspace, "blueprint-task", session_id, "preview_rebind")["result"]
    assert not scope["needs_approval"]
    completed = service.blueprint_task(session_id, "next")
    assert completed["status"] == "complete", completed
    assert completed["outputs"] == example["expected"]
    assert completed["parent_run_id"] == original_run
    assert completed["nodes"]["read"]["inherited_from"] == {
        "run_id": original_run,
        "outputs_hash": read_hash,
    }
    active_run = completed["active_run_id"]
    active = read_json(GraphSession(runtime, "job_" + active_run.removeprefix("run_")).path)
    assert _chain_tools(active) == [replacement["tool"], compare["tool"]]
    assert service.blueprint_task(session_id, "next")["active_run_id"] == active_run
    activation = workspace / "activate-rebind.json"
    activation.write_text(json.dumps({"scope_hash": scope["scope_hash"]}), encoding="utf-8")
    replay = cli(
        workspace, "blueprint-task", session_id, "activate_rebind", "--payload", activation
    )
    assert replay["result"]["active_run_id"] == active_run
    with closing(runtime.connect()) as db:
        rows = db.execute(
            "SELECT run_id, job_id FROM run_executions WHERE run_id IN (?, ?)",
            (original_run, active_run),
        ).fetchall()
        assert len(rows) == 2 and len({row["job_id"] for row in rows}) == 1
