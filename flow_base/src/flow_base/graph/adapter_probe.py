"""Offline conformance and behavior observations for a trusted local adapter."""

import json
import sys
import time

from routinekeel_contracts.domain_assets import canonical_json, content_hash
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry

from ..runtime import FlowError
from .qualification import LOCAL_PREREQUISITE_ERRORS, execute_local


def measure_local(request):
    bindings = request["binding"]
    node = request["node"]
    deadline = request.get("limit_ms")
    conformance = []
    observations = []
    for case in request["cases"]:
        started = time.monotonic_ns()
        failure = None
        outputs = None
        try:
            outputs = execute_local(bindings, node, case["inputs"], node["parameters"], workspace=request.get("workspace"))
        except (ValueError, KeyError, TypeError):
            failure = "ADAPTER_INPUT_REJECTED"
        except FlowError as error:
            failure = error.code
        elapsed_ms = (time.monotonic_ns() - started) // 1_000_000
        blocked = failure in LOCAL_PREREQUISITE_ERRORS
        valid = failure in {None, "ADAPTER_INPUT_REJECTED"} and (
            deadline is None or elapsed_ms <= deadline
        )
        conformance.append(
            {
                "id": case["id"], "status": "NOT_RUN" if blocked else "PASS" if valid else "FAIL",
                "elapsed_ms": elapsed_ms,
                **({"error": failure} if blocked else {}),
            }
        )
        if case["expected_error"] is not None:
            passed = failure == case["expected_error"]
        elif schemas := case.get("expected_schema"):
            passed = failure is None and all(
                name in outputs and Draft202012Validator(
                    schema, registry=Registry(), format_checker=FormatChecker(),
                ).is_valid(outputs[name])
                for name, schema in schemas.items()
            )
        else:
            passed = failure is None and content_hash(outputs) == content_hash(case["expected"])
        observations.append(
            {
                "id": case["id"],
                "status": "NOT_RUN" if blocked else "PASS" if passed else "FAIL",
                "input_hash": content_hash(case["inputs"]),
                "output_hash": content_hash(outputs),
                "error": failure,
            }
        )

    def summary(rows):
        statuses = {row["status"] for row in rows}
        status = "FAIL" if "FAIL" in statuses else "NOT_RUN" if "NOT_RUN" in statuses else "PASS"
        blockers = sorted({row["error"] for row in rows if row["status"] == "NOT_RUN"})
        return {
            "status": status,
            **({"code": blockers[0]} if status == "NOT_RUN" and len(blockers) == 1 else {}),
            "cases": rows,
        }

    return {"conformance": summary(conformance), "behavior": summary(observations)}


if __name__ == "__main__":
    raw = sys.stdin.buffer.read(2 * 1024**2 + 1)
    if len(raw) > 2 * 1024**2:
        raise SystemExit(2)
    output = measure_local(json.loads(raw))
    sys.stdout.buffer.write(canonical_json(output))
