"""Run a closed Pattern using the existing Blueprint Builder and its isolated verifier."""

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from routinekeel_contracts.asset_loading import load_asset_bytes
from routinekeel_contracts.domain_assets import (
    ProfileSchema,
    bind_profile_parameters,
    canonical_json,
)
from routinekeel_contracts.errors import StudioError
from routinekeel_contracts.graph import ForEach, Repeat, Task

from ..core.service import WorkflowService
from ..runtime import FlowError, atomic_json, read_json
from .capabilities import BUILTINS, resolve_graph


def _fixture_tools(graph):
    tools = {}

    def visit(body):
        for node in body.nodes:
            if isinstance(node, (ForEach, Repeat)):
                visit(node.body)
            elif isinstance(node, Task):
                if node.capability in BUILTINS and node.capability_version == "1.0.0":
                    node.capability_version = "1"
                elif node.capability not in BUILTINS and not node.capability.startswith(
                    "unresolved."
                ):
                    name = f"pattern-fixture-{len(tools) + 1}"
                    tools[name] = {
                        "version": node.capability_version,
                        "receipt_id": "isolated-pattern-fixture",
                        "available": True,
                        "allowed": True,
                        "platforms": [sys.platform],
                        "permissions": node.permissions,
                        "side_effect": node.side_effect,
                        "destination": "isolated fixture",
                        "network": False,
                        "resources": {"model_calls": 0, "cost_micro_usd": 0, "network": False},
                        "inputs": {
                            key: value.port.model_dump(exclude_none=True)
                            for key, value in node.inputs.items()
                        },
                        "outputs": {
                            key: value.model_dump(exclude_none=True)
                            for key, value in node.outputs.items()
                        },
                        "parameters_schema": {"type": "object", "const": node.parameters},
                    }
                    node.capability = name

    visit(graph)
    return tools


def run_pattern_trial(pattern_payload, cases, profile_schemas=None, profile_values=None):
    asset = load_asset_bytes("pattern", canonical_json(pattern_payload))
    model = asset.model
    if model.boundary.control_inputs or model.boundary.control_outputs:
        return {"verdict": "NOT_RUN", "code": "PATTERN_CONTROL_HARNESS_REQUIRED"}
    if any(rule.portability == "unresolved" for rule in model.requirements):
        return {"verdict": "NOT_RUN", "code": "CAPABILITY_UNAVAILABLE"}
    if not isinstance(cases, list) or not 1 <= len(cases) <= 20:
        return {"verdict": "NOT_RUN", "code": "TRIAL_CASES_REQUIRED"}
    schemas = {}
    for key, definition in (profile_schemas or {}).items():
        schemas[key] = ProfileSchema.model_validate(definition)
    try:
        bound = bind_profile_parameters(model, schemas, profile_values or {})
    except (ValueError, KeyError, TypeError):
        return {"verdict": "NOT_RUN", "code": "PATTERN_PROFILE_BINDINGS_REQUIRED"}
    tools = _fixture_tools(bound)
    graph = {
        **bound.model_dump(mode="json", exclude_none=True),
        "blueprint_schema_version": 2,
        "graph_contract_version": "2.2",
        "workflow": {
            "id": "pattern_trial",
            "goal": model.pattern.title,
            "revision": 1,
            "version": model.pattern.version,
        },
        "quality_contract": {**model.quality_contract, "golden_cases": cases},
        "provenance": {"pattern_ref": model.pattern.ref, "pattern_hash": asset.sha256},
    }
    with tempfile.TemporaryDirectory(prefix="routinekeel-pattern-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        catalog_path = root / "host-fixtures.json"
        catalog = {"version": 1, "expires_at": "2099-01-01T00:00:00+00:00"}
        previous = os.environ.get("ROUTINEKEEL_HOST_CATALOG")
        try:
            os.environ["ROUTINEKEEL_HOST_CATALOG"] = str(catalog_path)
            atomic_json(catalog_path, {**catalog, "tools": {}})
            absent = resolve_graph(graph, workspace, offline=True)
            atomic_json(catalog_path, {**catalog, "tools": tools})
            archive_path = workspace / "design.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("design/blueprint.json", canonical_json(graph))
            service = WorkflowService(workspace)
            draft = service.install_workflow(archive=archive_path)
            key = draft["draft_id"]
            resolution = service.resolve_blueprint_capabilities(key, offline=True)
            if resolution["status"] != "ready":
                return {
                    "verdict": "NOT_RUN",
                    "code": "CAPABILITY_UNAVAILABLE",
                    "gap_count": len(resolution["gaps"]),
                }
            try:
                build = service.install_workflow(
                    draft_id=key, resolution={"capability_fingerprint": resolution["fingerprint"]}
                )
            except FlowError as error:
                return {"verdict": "FAIL", "code": error.code}
            evidence = build["evidence"]
            archive_root = Path(build["archive"]).parent
            results = []
            for case in evidence["cases"]:
                outputs = read_json(archive_root / case["artifacts"] / "results.json")
                results.append(
                    {
                        "run_id": case["run_id"],
                        "outputs": outputs,
                        "outputs_hash": case["observed_outputs_hash"],
                        "simulated_host_and_review": case["simulated_host_and_review"],
                    }
                )
            return {
                "verdict": "PASS",
                "provider_calls": evidence["provider_calls"],
                "missing_fixture_preflight": absent["status"],
                "candidate_hash": build["archive_sha256"],
                "builder_receipt": build["builder_receipt"],
                "results": results,
            }
        finally:
            if previous is None:
                os.environ.pop("ROUTINEKEEL_HOST_CATALOG", None)
            else:
                os.environ["ROUTINEKEEL_HOST_CATALOG"] = previous


def main():
    try:
        raw = sys.stdin.buffer.read(2 * 1024**2 + 1)
        if len(raw) > 2 * 1024**2:
            raise ValueError("Pattern trial request too large")
        request = json.loads(raw)
        result = run_pattern_trial(
            request["pattern"],
            request["cases"],
            request.get("profile_schemas"),
            request.get("profile_values"),
        )
    except (FlowError, StudioError, OSError, TypeError, ValueError, KeyError) as error:
        result = {"verdict": "FAIL", "code": type(error).__name__}
    sys.stdout.buffer.write(canonical_json(result))


if __name__ == "__main__":
    main()
