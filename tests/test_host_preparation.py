"""Exercise host configuration against a separately running, local test service."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from flow_base.blueprints.completion_store import CompletionStore
from flow_base.core.service import WorkflowService
from flow_base.graph.external import record_observation
from flow_base.runtime import FlowError, digest
from routinekeel_contracts.domain_assets import content_hash
from test_blueprint_completion import wait_completion
from test_capability_resolver import adapter, observed_inventory  # noqa: F401
from test_graph_builder import import_graph
from test_toolchain_completion import report_graph

SERVICE = """
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
data = json.load(sys.stdin)
state = json.loads(path.read_bytes()) if path.exists() else {"label": None, "writes": 0, "operations": {}}
action = data["action"]
if action == "status":
    result = state
elif action == "read":
    result = state["operations"].get(data["operation_id"])
elif action == "compute":
    assert state["label"] is not None
    result = {"result": state["label"] + data["source"].upper()}
else:
    key = data["operation_id"]
    if key not in state["operations"]:
        state["label"] = data["arguments"].get("label")
        state["writes"] += 1
        state["operations"][key] = {"provider_receipt": f"local-setup-{state['writes']}", "label": state["label"]}
        path.write_text(json.dumps(state), encoding="utf-8")
    result = state["operations"][key]
print(json.dumps(result))
"""


def test_host_setup_recovers_without_redispatch_and_compensates(observed_inventory, monkeypatch):  # noqa: F811
    workspace = observed_inventory()
    service_path = workspace.parent / "host" / "test-service.json"
    destination = "https://fixture.invalid/service"

    def call_service(action, **data):
        response = subprocess.run(
            [sys.executable, "-I", "-c", SERVICE, str(service_path)],
            input=json.dumps({"action": action, **data}),
            encoding="utf-8",
            capture_output=True,
            timeout=10,
            check=True,
        )
        return json.loads(response.stdout)

    def action(tool, schema):
        return {
            "tool": tool,
            "version": "1.0.0",
            "receipt_id": "fixture-" + tool,
            "available": True,
            "allowed": True,
            "platforms": [sys.platform],
            "side_effect": "external",
            "network": True,
            "destination": destination,
            "permissions": ["configure"],
            "resources": {"cost_micro_usd": 0, "model_calls": 0},
            "input_schema": {
                "type": "object",
                "properties": schema,
                "required": list(schema),
                "additionalProperties": False,
            },
        }

    configure = action("fixture.configure_service", {"label": {"type": "string"}})
    restore = action("fixture.restore_service", {})

    def declared(action, arguments):
        return {
            "tool": action["tool"],
            "version": action["version"],
            "destination": destination,
            "arguments": arguments,
            "permissions": action["permissions"],
            "cost_micro_usd": 0,
        }

    text = {"type": "string", "required": True}
    target = adapter("fixture.configured_tool", cap="fixture.configured_contract")
    target.update(
        inputs={"source": text},
        outputs={"result": text},
        destination=destination,
        host_preparation={
            "recipe_schema_version": 1,
            "id": "fixture.service_setup",
            "version": "1.0.0",
            "configure": declared(configure, {"label": "[READY]"}),
            "compensate": declared(restore, {}),
        },
    )
    target["portable"].update(inputs=target["inputs"], outputs=target["outputs"])

    def observe(proof=None):
        label = call_service("status")["label"]
        row = {
            **target,
            "available": label is not None,
            "configuration_fingerprint": digest(label) if label is not None else None,
            "receipt_id": "fixture-target-" + digest(label),
        }
        if proof:
            row["portable_qualification"] = proof
        observed_inventory(configure, restore, row)
        return row

    observe()
    graph = report_graph()
    graph.update(
        graph_contract_version="2.3",
        inputs={"source": text},
        outputs={"result": "artifact://reconcile/result"},
    )
    node = graph["nodes"][0]
    node.update(
        capability="fixture.configured_contract",
        inputs={"source": {"source": "input://source", "port": text}},
        outputs={"result": text},
        capability_requirement={
            "id": "configured_tool",
            "capability": "fixture.configured_contract",
            "version": "1.0.0",
            "required": True,
            "portability": "flexible",
        },
    )
    cases = [
        {"inputs": {"source": value}, "expected": {"result": expected}}
        for value, expected in [("one", "[READY]ONE"), ("two", "[READY]TWO")]
    ]
    graph["quality_contract"] = {
        "golden_cases": [
            {
                **case,
                "replies": [{"step": "reconcile", "kind": "host", "outputs": case["expected"]}],
            }
            for case in cases
        ],
        "adapter_cases": {
            "reconcile": [{"id": f"case_{n}", **case} for n, case in enumerate(cases)]
        },
    }
    service, draft, _ = import_graph(workspace, graph)

    def command(state, action, operation, request_id, **fields):
        return {
            "action": action,
            "operation_id": operation["operation_id"],
            "scope_hash": operation["scope_hash"],
            "expected_revision": state["revision"],
            "request_id": request_id,
            **fields,
        }

    def submit(state, action, operation, request_id, **fields):
        return service.blueprint_completion(
            "submit", state["work_id"], command(state, action, operation, request_id, **fields)
        )

    def start(request_id):
        state = service.blueprint_completion(
            "start", payload={"draft_id": draft, "intent": "install", "request_id": request_id}
        )
        (offer,) = state["gaps"][0]["host_setup_offers"]
        state = submit(
            state,
            "authorize_host_preparation",
            offer,
            f"{request_id}-authorize",
            user_text="核准此測試服務的具名配置",
        )
        return state, offer

    first, offer = start("first")
    second, other = start("second")
    payload = command(first, "claim_host_preparation", offer, "first-claim")
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                WorkflowService(workspace).blueprint_completion, "submit", first["work_id"], payload
            )
            for _ in range(2)
        ]
        replies = [future.result() for future in futures]
    (claimed,) = [reply for reply in replies if "host_dispatch" in reply]
    dispatch = claimed["host_dispatch"]
    assert dispatch["operation_id"] == offer["operation_id"] and dispatch["arguments"] == {
        "label": "[READY]"
    }
    with pytest.raises(FlowError, match="服務已有待查操作"):
        submit(second, "claim_host_preparation", other, "concurrent-claim")
    call_service(**dispatch)

    # The host connection stopped after the actual write, before submitting its receipt.
    service = WorkflowService(workspace)
    replay = service.blueprint_completion("submit", first["work_id"], payload)
    assert "host_dispatch" not in replay
    waiting = submit(replay, "reconcile_host_preparation", offer, "no-receipt")
    assert waiting["host_operations"][offer["operation_id"]]["status"] == "claimed"
    assert call_service("status")["writes"] == 1
    result = call_service("read", operation_id=offer["operation_id"])
    record_observation(
        workspace,
        offer["operation_id"],
        offer["scope_hash"],
        result["provider_receipt"],
        "succeeded",
        {
            "target_hash": offer["scope"]["target_hash"],
            "configuration_fingerprint": digest(result["label"]),
        },
        usage={"cost_micro_usd": 0, "model_calls": 0},
    )
    waiting = submit(waiting, "reconcile_host_preparation", offer, "no-new-observation")
    assert waiting["host_operations"][offer["operation_id"]]["status"] == "receipt_verified"
    observe()

    # Lose the completion revision after durable reconciliation, and recover from its journal.
    with monkeypatch.context() as patch:

        def interrupted(*_args, **_kwargs):
            raise OSError("lost response after reconciliation")

        patch.setattr(CompletionStore, "save", interrupted)
        with pytest.raises(OSError, match="lost response"):
            submit(waiting, "reconcile_host_preparation", offer, "lost-reconcile")
    service = WorkflowService(workspace)
    configured = submit(waiting, "reconcile_host_preparation", offer, "recovered")
    assert configured["host_operations"][offer["operation_id"]]["status"] == "verified"
    with pytest.raises(FlowError, match="範圍已變更"):
        submit(second, "claim_host_preparation", other, "old-authorization")
    assert call_service("status")["writes"] == 1

    (gap,) = configured["gaps"]
    (candidate,) = gap["candidates"]
    selected = service.blueprint_completion(
        "submit",
        configured["work_id"],
        {
            "action": "select_adapter",
            "adapter": candidate["adapter"],
            "gap_id": gap["id"],
            "scope_hash": gap["scope_hash"],
            "request_id": "select-configured",
            "expected_revision": configured["revision"],
            "user_text": "採用剛完成配置的測試功能",
        },
    )
    scope = selected["qualification"]["reconcile"]["scope"]
    observe(
        {
            "observation": "executed",
            "request_hash": scope["request_hash"],
            "case_hash": scope["case_hashes"]["reconcile"],
            "environment_fingerprint": candidate["environment_fingerprint"],
            "authorization_hash": digest("fixture behavior authorization"),
            "conformance": {
                key: "PASS"
                for key in ("inputs", "outputs", "permissions", "timeout", "side_effects", "budget")
            },
            "results": [
                {
                    "id": f"case_{n}",
                    "input_hash": content_hash(case["inputs"]),
                    "cost_micro_usd": 0,
                    "output_hash": content_hash(call_service("compute", **case["inputs"])),
                }
                for n, case in enumerate(cases)
            ],
        }
    )
    queued = service.blueprint_completion(
        "recheck",
        selected["work_id"],
        {
            "request_id": "behavior-observed",
            "expected_revision": selected["revision"],
        },
    )
    installed = wait_completion(service, queued, ("installed", "needs_input"))
    assert installed["status"] == "installed", installed.get("gaps")

    staged = submit(installed, "request_host_compensation", offer, "request-compensation")
    (compensation,) = [
        row for row in staged["host_operations"].values() if row["scope"]["action"] == "compensate"
    ]
    authorized = submit(
        staged,
        "authorize_host_preparation",
        compensation,
        "authorize-compensation",
        user_text="核准回復這次測試設定",
    )
    restore_claim = submit(authorized, "claim_host_preparation", compensation, "claim-compensation")
    result = call_service(**restore_claim["host_dispatch"])
    record_observation(
        workspace,
        compensation["operation_id"],
        compensation["scope_hash"],
        result["provider_receipt"],
        "succeeded",
        {"target_hash": offer["scope"]["target_hash"], "configuration_fingerprint": None},
        usage={"cost_micro_usd": 0, "model_calls": 0},
    )
    observe()
    restored = submit(
        restore_claim, "reconcile_host_preparation", compensation, "reconcile-compensation"
    )
    assert restored["host_operations"][offer["operation_id"]]["status"] == "compensated"
    assert call_service("status")["writes"] == 2 and call_service("status")["label"] is None
    (retry,) = restored["gaps"][0]["host_setup_offers"]
    assert (
        retry["operation_id"] != offer["operation_id"]
        and retry["scope"]["replaces"] == offer["operation_id"]
    )
    retry_auth = submit(
        restored,
        "authorize_host_preparation",
        retry,
        "authorize-noop",
        user_text="核准新的具名配置",
    )
    retry_claim = submit(retry_auth, "claim_host_preparation", retry, "claim-noop")
    assert call_service("read", operation_id=retry["operation_id"]) is None
    record_observation(
        workspace,
        retry["operation_id"],
        retry["scope_hash"],
        "provider-noop",
        "not_executed",
        {"target_hash": retry["scope"]["target_hash"], "configuration_fingerprint": None},
        usage={"cost_micro_usd": 0, "model_calls": 0},
    )
    observe()
    noop = submit(retry_claim, "reconcile_host_preparation", retry, "reconcile-noop")
    (followup,) = noop["gaps"][0]["host_setup_offers"]
    assert followup["scope"]["replaces"] == retry["operation_id"]
    assert call_service("status")["writes"] == 2


def test_setup_offer_can_be_a_prerequisite_of_an_unbound_chain(observed_inventory):  # noqa: F811
    text = {"type": "string", "required": True}
    source = adapter("fixture.source_tool", cap="fixture.raw_source")
    reader = adapter("fixture.reader_tool", cap="fixture.reader")
    for tool, before, after in (
        (source, "fixture.raw", "fixture.middle"),
        (reader, "fixture.middle", "fixture.final"),
    ):
        tool.update(inputs={"source": text}, outputs={"result": text})
        tool["portable"].update(
            inputs={"source": {**text, "schema_id": before}},
            outputs={"result": {**text, "schema_id": after}},
        )
    destination = "https://fixture.invalid/normalize"
    grant = {
        "tool": "fixture.setup_source",
        "version": "1.0.0",
        "destination": destination,
        "permissions": ["configure"],
        "cost_micro_usd": 0,
    }
    configuration = {
        **grant,
        "available": True,
        "allowed": True,
        "platforms": [sys.platform],
        "receipt_id": "fixture-observed-setup",
        "side_effect": "external",
        "network": True,
        "resources": {"cost_micro_usd": 0, "model_calls": 0},
        "input_schema": {"type": "object"},
    }
    source.update(
        available=False,
        destination=destination,
        host_preparation={
            "recipe_schema_version": 1,
            "id": "fixture.chain_setup",
            "version": "1.0.0",
            "configure": {**grant, "arguments": {}},
        },
    )
    workspace = observed_inventory(source, reader, configuration)
    graph = report_graph()
    graph.update(
        graph_contract_version="2.3",
        inputs={"source": text},
        outputs={"result": "artifact://reconcile/result"},
    )
    node = graph["nodes"][0]
    node.update(
        inputs={"source": {"source": "input://source", "port": text}},
        outputs={"result": text},
        capability_requirement={
            "id": "chain",
            "capability": node["capability"],
            "required": True,
            "portability": "flexible",
            "input_semantics": {"source": "fixture.raw"},
            "output_semantics": {"result": "fixture.final"},
        },
    )
    graph["quality_contract"] = {
        "golden_cases": [{"inputs": {"source": "one"}, "expected": {"result": "ONE"}}]
    }
    service, draft, _ = import_graph(workspace, graph)
    state = service.blueprint_completion(
        "start", payload={"draft_id": draft, "request_id": "prospective-config"}
    )
    (gap,) = state["gaps"]
    assert not gap["candidates"] and not gap["toolchain_planning"]["plans"]
    (offer,) = gap["host_setup_offers"]
    assert offer["scope"]["target"]["tool"] == source["tool"]
