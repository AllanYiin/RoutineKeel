"""Real construction, qualification, candidate installation and runtime replay."""

import json
import os
import shutil
import sys

import pytest
from flow_base.graph.preparation import (
    execute_prepared,
    load_recipe,
    preparation_id,
    prepare_recipe,
)
from flow_base.runtime import FlowError, sha256
from routinekeel_contracts.base_steps import DESIGN_KEY, DOCUMENT_SEARCH, task_node
from test_base_steps import _docx
from test_blueprint_completion import wait_completion
from test_capability_resolver import adapter, observed_inventory  # noqa: F401
from test_graph_builder import import_graph
from test_toolchain_completion import report_graph


def _record(recipe, capability, inputs, outputs):
    entry = adapter(recipe["tool"], cap=capability, locus="local_runtime")
    entry.update(inputs=inputs, outputs=outputs, preparation=recipe, preparation_allowed=True)
    entry["portable"].update(distribution=None, inputs=inputs, outputs=outputs)
    return entry


def _node(capability, inputs, outputs):
    return {
        "id": "transform",
        "kind": "task",
        "capability": capability,
        "capability_version": "1.0.0",
        "permissions": [],
        "side_effect": "none",
        "inputs": inputs,
        "outputs": outputs,
        "capability_requirement": {
            "id": "transform",
            "capability": capability,
            "version": "1.0.0",
            "required": True,
            "portability": "flexible",
        },
    }


def _install(workspace, graph, *, timeout=45):
    service, draft, _ = import_graph(workspace, graph)
    started = service.blueprint_completion(
        "start",
        payload={
            "draft_id": draft,
            "intent": "install",
            "offline": True,
            "request_id": "construct-install",
        },
    )
    result = wait_completion(service, started, ("installed", "needs_input"), timeout=timeout)
    assert result["status"] == "installed", result.get("gaps")
    return service, draft, result


def _require_program(command):
    if shutil.which(command) is None:
        if os.environ.get("ROUTINEKEEL_E2E_ENFORCE") == "1":
            pytest.fail(f"{command} is required for the construction acceptance case")
        pytest.skip(f"{command} is not installed")


def test_constructed_mapping_delivers_documa_references(observed_inventory):  # noqa: F811
    _require_program("documa")
    workspace = observed_inventory()
    directory = workspace / "fixtures"
    directory.mkdir()
    first, second = directory / "first.docx", directory / "second.docx"
    _docx(first, "reconciliation references for the first file")
    _docx(second, "reconciliation references for the second file")
    rows = {"type": "array", "required": True}
    recipe = {
        "recipe_schema_version": 1,
        "id": "fixture.source_citations",
        "version": "1.0.0",
        "action": "construct_mapping",
        "tool": "fixture.citation_projection",
        "platforms": [sys.platform],
        "outputs": {
            "references": {"source": "matches", "fields": {"source": "/source", "route": "/route"}}
        },
    }
    capability = "fixture.document_references"
    observed_inventory(_record(recipe, capability, {"matches": rows}, {"references": rows}))
    graph = report_graph()
    graph.update(
        graph_contract_version="2.3",
        inputs={
            "file": {"type": "file"},
            "additional": {"type": "file", "required": False},
            "query": {"type": "string"},
            "document_indexes": {"type": "object"},
        },
        nodes=[
            task_node(
                DOCUMENT_SEARCH,
                "search",
                {"indexes": "input://document_indexes", "query": "input://query"},
                parameters={"route": "graph-first"},
            ),
            _node(
                capability,
                {"matches": {"source": "artifact://search/matches", "port": rows}},
                {"references": rows},
            ),
        ],
        outputs={"references": "artifact://transform/references"},
    )
    cases = []
    for files in [[first], [first, second]]:
        source = {"file": {"$file": "fixtures/first.docx"}, "query": "reconciliation"}
        if len(files) > 1:
            source["additional"] = {"$file": "fixtures/second.docx"}
        expected = {
            "references": [
                {"source": reference, "route": "graph-first"}
                for reference in sorted(f"asset:{sha256(path)}.docx" for path in files)
            ]
        }
        cases.append({"inputs": source, "expected": expected})
    graph["quality_contract"] = {
        DESIGN_KEY: 1,
        "golden_cases": cases,
        "adapter_cases": {
            "transform": [
                {
                    "id": "one",
                    "inputs": {
                        "matches": [
                            {"source": "fixture/one", "route": "graph-first", "snippet": "one"}
                        ]
                    },
                    "expected": {"references": [{"source": "fixture/one", "route": "graph-first"}]},
                },
                {
                    "id": "two",
                    "inputs": {"matches": [{"source": "fixture/two", "route": "direct"}]},
                    "expected": {"references": [{"source": "fixture/two", "route": "direct"}]},
                },
                {
                    "id": "missing",
                    "inputs": {"matches": [{"route": "graph-first"}]},
                    "expected_error": "ADAPTER_INPUT_REJECTED",
                },
            ]
        },
    }
    service, _draft, result = _install(workspace, graph)
    assert result["preparations"]["transform"]["receipt"]["action"] == "construct_mapping"
    assert result["qualification"]["transform"]["report"]["verdict"] == "PASS"
    request = workspace / "document-request.json"
    request.write_text(json.dumps({"inputs": cases[1]["inputs"]}), encoding="utf-8")
    run = service.run_workflow_sync(
        {"task": graph["workflow"]["id"], "source": request, "offline": True}
    )
    assert run["blueprint"]["outputs"] == cases[1]["expected"]


def _identity_program(memory_pages=1):
    def section(key, body):
        assert len(body) < 128
        return bytes([key, len(body)]) + body

    def export(name, kind, index):
        return bytes([len(name)]) + name.encode() + bytes([kind, index])

    memory = (
        bytes([1, 1, memory_pages, memory_pages])
        if memory_pages < 128
        else bytes([1, 1, 0x81, 1, 0x81, 1])
    )
    # The program writes no output outside its memory. transform returns (pointer << 32) | length.
    return b"\0asm\x01\0\0\0" + b"".join(
        [
            section(1, bytes([2, 0x60, 1, 0x7F, 1, 0x7F, 0x60, 2, 0x7F, 0x7F, 1, 0x7E])),
            section(3, bytes([2, 0, 1])),
            section(5, memory),
            section(
                7,
                bytes([3])
                + export("memory", 2, 0)
                + export("allocate", 0, 0)
                + export("transform", 0, 1),
            ),
            section(
                10,
                bytes(
                    [
                        2,
                        4,
                        0,
                        0x41,
                        0,
                        0x0B,
                        12,
                        0,
                        0x20,
                        0,
                        0xAD,
                        0x42,
                        0x20,
                        0x86,
                        0x20,
                        1,
                        0xAD,
                        0x84,
                        0x0B,
                    ]
                ),
            ),
        ]
    )


def test_constructed_wasm_is_verified_installed_and_rechecked(observed_inventory):  # noqa: F811
    _require_program("node")
    workspace = observed_inventory()
    host = workspace.parent / "host"
    artifact = host / "echo.wasm"
    artifact.write_bytes(_identity_program())
    recipe = {
        "recipe_schema_version": 1,
        "id": "fixture.isolated_echo",
        "version": "1.0.0",
        "action": "construct_wasm",
        "tool": "fixture.wasm_echo",
        "platforms": [sys.platform],
        "artifact": artifact.name,
        "sha256": sha256(artifact),
    }
    text = {"type": "string", "required": True}
    capability = "fixture.isolated_construction"
    observed_inventory(_record(recipe, capability, {"source": text}, {"source": text}))
    graph = report_graph()
    graph.update(
        graph_contract_version="2.3",
        inputs={"source": text},
        nodes=[
            _node(
                capability, {"source": {"source": "input://source", "port": text}}, {"source": text}
            )
        ],
        outputs={"source": "artifact://transform/source"},
    )
    cases = [
        {"inputs": {"source": word}, "expected": {"source": word}} for word in ("alpha", "測試")
    ]
    graph["quality_contract"] = {
        "golden_cases": cases,
        "adapter_cases": {
            "transform": [{"id": f"case_{index}", **case} for index, case in enumerate(cases)]
        },
    }
    service, draft, result = _install(workspace, graph, timeout=120)
    prepared = result["preparations"]["transform"]
    assert prepared["receipt"]["isolation"] == {"abi": 1, "imports": 0, "memory_pages": 128}
    request = workspace / "wasm-request.json"
    request.write_text('{"inputs":{"source":"installed"}}', encoding="utf-8")
    run = service.run_workflow_sync(
        {"task": graph["workflow"]["id"], "source": request, "offline": True}
    )
    assert run["blueprint"]["outputs"] == {"source": "installed"}

    binding = service.resolve_blueprint_capabilities(draft, offline=True)["bindings"]["transform"]
    with pytest.raises(FlowError) as rejected:
        execute_prepared(workspace, binding, {"source": "fixture"}, {"undeclared": 1})
    assert rejected.value.code == "ADAPTER_INPUT_REJECTED"
    oversized = host / "invalid-memory.wasm"
    oversized.write_bytes(_identity_program(memory_pages=129))
    invalid = load_recipe({**recipe, "artifact": oversized.name, "sha256": sha256(oversized)})
    with pytest.raises(FlowError, match="隔離檢查"):
        prepare_recipe(service.workspace.runtime, invalid, host)
    root = service.workspace.runtime.home / "toolchain_preparations"
    assert not (root / preparation_id(invalid) / "receipt.json").exists()

    mismatch = json.loads(json.dumps(graph))
    mismatch["quality_contract"]["adapter_cases"]["transform"][0]["expected"] = {
        "source": "independent expected value"
    }
    _service, moved, _ = import_graph(workspace, mismatch)
    failed = service.blueprint_completion(
        "start",
        payload={
            "draft_id": moved,
            "intent": "install",
            "offline": True,
            "request_id": "construct-mismatch",
        },
    )
    assert (
        failed["status"] == "needs_input"
        and failed["qualification"]["transform"]["report"]["verdict"] == "FAIL"
    )
    assert not failed.get("trial")

    registered = root / prepared["operation_id"] / "program.wasm"
    registered.write_bytes(registered.read_bytes() + b"altered")
    assert (
        service.resolve_blueprint_capabilities(draft, offline=True)["gaps"][0]["code"]
        == "BINDING_STALE"
    )
