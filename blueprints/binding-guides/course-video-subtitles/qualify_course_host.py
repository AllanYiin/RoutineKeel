"""Bind real copyediting observations to the authorized qualification scope."""

import json
from pathlib import Path
import sys

from jsonschema import Draft202012Validator

from flow_base.runtime import atomic_json
from flow_base.graph.model_budget import batch_count, usage_evidence
from routinekeel_contracts.domain_assets import content_hash


if __name__ == "__main__":
    catalog_path, status_path, cases_path, observations_path, approval_path, output_path = map(Path, sys.argv[1:])
    catalog, status, suites, observed, approval = [
        json.loads(path.read_bytes())
        for path in (catalog_path, status_path, cases_path, observations_path, approval_path)
    ]
    assert approval["action"] == "complete_adapter_cases" and approval["user_text"].strip()
    scope = status["qualification"]["edit_transcript"]["scope"]
    candidate = scope["adapters"]["edit_transcript"]
    cases = suites["edit_transcript"]
    assert approval["cases"] == cases
    case_hash = content_hash(cases)
    assert scope["case_hashes"]["edit_transcript"] == case_hash
    tool = catalog["tools"][candidate["tool"]]
    facts = observed["facts"]
    assert content_hash(facts) == tool["receipt_id"]
    assert facts["model_version"] == candidate["model_version"]
    assert tool["permissions"] == [] and tool["allowed"]
    assert tool["destination"] == "local Ollama" and not tool["network"]
    assert tool["resources"]["cost_micro_usd"] == candidate["cost_micro_usd"] == 0
    input_schema = facts["input_schema"]
    inputs_validator = Draft202012Validator({
        **input_schema,
        "required": [field for field in input_schema["required"] if field != "model_budget"],
    })
    resources = tool["resources"]
    assert resources["model_call_batch"] == facts["model_call_batch"] == candidate["model_budget"]["model_call_batch"]
    results = []
    budget_status = "PASS"
    output_status = "PASS"
    for case, observed_case in zip(cases, observed["observations"], strict=True):
        assert case["id"] == observed_case["id"]
        inputs_validator.validate(case["inputs"])
        required = batch_count(resources, case["inputs"])
        measured = observed_case.get("measurement")
        if not isinstance(measured, dict) or not usage_evidence(resources, measured):
            if budget_status == "PASS":
                budget_status = "NOT_RUN"
        elif (measured["model_calls"] != required or
              any(row["outcome"] != "succeeded" for row in measured["batches"])):
            budget_status = "FAIL"
        if observed_case["outcome"] != "succeeded" or case["expected"] != observed_case["output"]:
            output_status = "FAIL"
        results.append({
            "id": case["id"], "input_hash": content_hash(case["inputs"]),
            "output_hash": content_hash(observed_case["output"]), "cost_micro_usd": 0,
            "measurement": measured,
        })
    tool["portable_qualification"] = {
        "observation": "executed", "request_hash": scope["request_hash"],
        "environment_fingerprint": candidate["environment_fingerprint"],
        "case_hash": case_hash, "model_version": candidate["model_version"],
        "authorization_hash": content_hash(approval),
        "conformance": {
            "inputs": "PASS", "outputs": output_status, "permissions": "PASS", "side_effects": "PASS",
            "timeout": "PASS" if all(
                0 <= row["elapsed_ms"] <= max(30, batch_count(resources, case["inputs"]) * 185) * 1000
                for case, row in zip(cases, observed["observations"], strict=True)
            ) else "FAIL",
            "budget": budget_status,
        },
        "results": results,
    }
    atomic_json(output_path, catalog)
    print(f"Recorded {len(results)} authorized host observations")
