"""Prepare a pinned wheel via completion and exercise the actual installed candidate."""

import json
import os
import sys
from importlib.metadata import entry_points

import pytest
from flow_base.blueprints.wheel import write_wheel
from flow_base.graph.preparation import WheelPreparation, preparation_id, prepare_wheels
from flow_base.runtime import FlowError, sha256
from test_blueprint_completion import wait_completion
from test_capability_resolver import adapter, observed_inventory  # noqa: F401
from test_graph_builder import import_graph
from test_toolchain_completion import report_graph


def preparation_recipe(host):
    wheel = host / "rk_fixture_prepared-1.0.0-py3-none-any.whl"
    write_wheel(
        wheel,
        "rk_fixture_prepared",
        "1.0.0",
        {
            "rk_fixture_prepared/__init__.py": b'def apply(inputs, parameters):\n    return {"result": inputs["source"].upper()}\n',
            "rk_fixture_prepared-1.0.0.dist-info/entry_points.txt": (
                b"[routinekeel.portable_adapters]\nfixture.prepared=rk_fixture_prepared:apply\n"
            ),
        },
        dependencies=(),
    )
    return {
        "recipe_schema_version": 1,
        "id": "fixture.prepare_upper",
        "version": "1.0.0",
        "action": "install_locked_wheels",
        "tool": "fixture.prepared",
        "distribution": "rk-fixture-prepared",
        "platforms": [sys.platform],
        "wheels": [
            {
                "artifact": wheel.name,
                "sha256": sha256(wheel),
                "distribution": "rk-fixture-prepared",
                "version": "1.0.0",
            }
        ],
    }


@pytest.mark.parametrize("chained", [False, True], ids=["direct", "toolchain"])
def test_authorized_recipe_prepares_then_qualifies_and_runs(observed_inventory, chained):  # noqa: F811
    workspace = observed_inventory()
    host = workspace.parent / "host"
    recipe = preparation_recipe(host)
    text = {"type": "string", "required": True}
    tool = adapter("fixture.prepared", cap="fixture.prepared_contract", locus="local_runtime")
    tool.update(
        inputs={"source": text},
        outputs={"result": text},
        preparation=recipe,
        preparation_allowed=True,
    )
    tool["portable"].update(
        distribution="rk-fixture-prepared", inputs=tool["inputs"], outputs=tool["outputs"]
    )
    observed_inventory(tool)
    graph = report_graph()
    graph.update(
        graph_contract_version="2.3",
        inputs={"source": text},
        outputs={"result": "artifact://reconcile/result"},
    )
    node = graph["nodes"][0]
    node.update(
        capability="fixture.prepared_contract",
        inputs={"source": {"source": "input://source", "port": text}},
        outputs={"result": text},
        capability_requirement={
            "id": "prepared_tool",
            "capability": "fixture.prepared_contract",
            "version": "1.0.0",
            "required": True,
            "portability": "flexible",
        },
    )
    examples = [("one", "ONE"), ("two", "TWO"), ("three", "THREE")]
    if chained:
        names = {entry.name for entry in entry_points(group="routinekeel.portable_adapters")}
        if "routinekeel.csv_read" not in names:
            if os.environ.get("ROUTINEKEEL_E2E_ENFORCE") == "1":
                pytest.fail("The Data Adapter wheel is required for the preparation toolchain case")
            pytest.skip("The optional Data Adapter wheel is not installed")
        rows = {"type": "array", "required": True}
        tool["portable"].update(
            capability="fixture.normalize_csv",
            inputs={"source": {**text, "schema_id": "fixture.raw_csv"}},
            outputs={"result": {**text, "schema_id": "fixture.normalized_csv"}},
        )
        reader = adapter("routinekeel.csv_read", cap="fixture.csv_reader", locus="local_runtime")
        reader.update(inputs={"source": text}, outputs={"rows": rows})
        reader["portable"].update(
            inputs={"source": {**text, "schema_id": "fixture.normalized_csv"}},
            outputs={"rows": {**rows, "schema_id": "fixture.uppercase_rows"}},
        )
        observed_inventory(tool, reader)
        node["capability_requirement"].update(
            input_semantics={"source": "fixture.raw_csv"},
            output_semantics={"result": "fixture.uppercase_rows"},
        )
        node["outputs"] = {"result": rows}
        examples = [(f"name\n{value}\n", [{"NAME": expected}]) for value, expected in examples]
    cases = [
        {"inputs": {"source": value}, "expected": {"result": expected}}
        for value, expected in examples[:2]
    ]
    graph["quality_contract"] = {
        "golden_cases": cases,
        "adapter_cases": {
            "reconcile": [{"id": f"case_{index}", **case} for index, case in enumerate(cases)],
        },
    }
    service, draft, _ = import_graph(workspace, graph)
    initial = service.resolve_blueprint_capabilities(draft, offline=True)
    assert any(
        item["code"] == "DEPENDENCY_NOT_INSTALLED" and item.get("recipe_id") == recipe["id"]
        for item in initial["portable_decisions"][0]["rejections"]
    )
    request = {
        "draft_id": draft,
        "intent": "install",
        "offline": True,
        "request_id": "prepare-install",
    }
    started = service.blueprint_completion("start", payload=request)
    final = wait_completion(service, started, ("installed", "needs_input"))
    assert final["status"] == "installed", final.get("gaps")
    prepared = final["preparations"]["reconcile"]
    assert (
        prepared["status"] == "prepared"
        and final["qualification"]["reconcile"]["report"]["verdict"] == "PASS"
    )
    if chained:
        assert final["qualification"]["reconcile"]["report"]["toolchain_graph_hash"]
    assert service.blueprint_completion("start", payload=request) == started
    assert (
        prepare_wheels(service.workspace.runtime, WheelPreparation.model_validate(recipe), host)
        == prepared["receipt"]
    )
    path = workspace / "input.json"
    path.write_text(json.dumps({"inputs": {"source": examples[2][0]}}), encoding="utf-8")
    output = service.run_workflow_sync(
        {"task": "toolchain_report", "source": path, "offline": True}
    )
    assert output["blueprint"]["outputs"] == {"result": examples[2][1]}

    runtime = service.workspace.runtime
    bad = json.loads(json.dumps(recipe))
    bad["wheels"][0]["sha256"] = "f" * 64
    invalid = WheelPreparation.model_validate(bad)
    with pytest.raises(FlowError, match="校驗值"):
        prepare_wheels(runtime, invalid, host)
    target = runtime.home / "toolchain_preparations"
    assert not (target / preparation_id(invalid) / "receipt.json").exists()
    path = target / prepared["operation_id"] / "site/rk_fixture_prepared/__init__.py"
    path.write_bytes(path.read_bytes() + b"\n# drift\n")
    stale = service.resolve_blueprint_capabilities(draft, offline=True)
    assert stale["gaps"][0]["code"] == "BINDING_STALE"
