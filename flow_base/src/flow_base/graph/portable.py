"""Resolve portable requirements from observed local and desktop tool inventories."""

import os
import sys
from hashlib import sha256
from importlib.metadata import entry_points

from pydantic import ValidationError
from routinekeel_contracts.bindings import ImplementationAdapter
from routinekeel_contracts.domain_assets import CapabilityRequirement, content_hash
from routinekeel_contracts.graph_validator import compatible
from routinekeel_contracts.pattern_composition import PatternComposition

from ..runtime import FlowError

ADAPTER_GROUP = "routinekeel.portable_adapters"


def graph_requirements(graph):
    return {
        key: rule
        for source in graph.provenance.get("pattern_compositions", [])
        for key, rule in PatternComposition.model_validate(source).requirements.items()
    }


def local_entry(adapter):
    if adapter.locus != "local_runtime" or not adapter.distribution:
        return None
    matches = [
        entry
        for entry in entry_points(group=ADAPTER_GROUP)
        if entry.name == adapter.tool
        and entry.dist is not None
        and entry.dist.name == adapter.distribution
        and entry.dist.version == adapter.provider_version
    ]
    if len(matches) != 1:
        return None
    entry = matches[0]
    record = entry.dist.read_text("RECORD")
    if not record:
        return None
    source = entry.dist.locate_file(entry.module.replace(".", "/") + ".py")
    try:
        if not source.is_file() or source.is_symlink() or source.stat().st_size > 2 * 1024**2:
            return None
        code_hash = sha256(source.read_bytes()).hexdigest()
    except OSError:
        return None
    return entry, content_hash([entry.name, entry.value, record, code_hash])


def _builtin(node, rule, workspace):
    from .capabilities import BUILTINS, resolve_task

    if node.capability not in BUILTINS or rule.version not in {None, "1.0.0"}:
        return None
    if node.capability_version not in {"1", "1.0.0"}:
        return None
    try:
        binding = resolve_task(node.model_copy(update={"capability_version": "1"}), workspace)
    except FlowError:
        return None
    tool_id = "routinekeel." + node.capability.replace(".", "_")
    adapter = ImplementationAdapter(
        id=tool_id,
        version="1.0.0",
        capability=rule.capability,
        capability_version="1.0.0",
        provider_version="1",
        locus="local_runtime",
        tool=node.capability,
        inputs={key: item.port.model_dump() for key, item in node.inputs.items()},
        outputs={key: port.model_dump() for key, port in node.outputs.items()},
        permissions=[],
        side_effect="none",
        network=False,
        platforms=[sys.platform],
        max_cost_micro_usd=0,
    )
    return adapter, binding, binding["fingerprint"], True


def _catalog_adapters(node, workspace, offline):
    from .capabilities import host_catalog, resolve_task

    if not os.environ.get("ROUTINEKEEL_HOST_CATALOG"):
        return [], True
    try:
        inventory = host_catalog(workspace)
    except FlowError:
        return [], True
    observations = []
    for name, record in inventory.items():
        if not isinstance(record, dict) or "portable" not in record or not record.get("receipt_id"):
            continue
        try:
            adapter = ImplementationAdapter.model_validate(record["portable"])
        except ValidationError:
            continue
        if (
            adapter.tool != name
            or adapter.provider_version != record.get("version")
            or set(adapter.permissions) != set(record.get("permissions", []))
            or adapter.side_effect != record.get("side_effect")
            or adapter.network != record.get("network")
            or adapter.max_cost_micro_usd
            != (record.get("resources") or {}).get("cost_micro_usd", 0)
        ):
            continue
        if not record.get("available") or sys.platform not in record.get("platforms", []):
            continue
        granted = record.get("allowed") is True
        if adapter.locus == "local_runtime":
            installed = local_entry(adapter)
            if not installed:
                continue
            _entry, proof = installed
            binding = {
                "provider": "portable_local",
                "tool": name,
                "version": adapter.provider_version,
                "permissions": adapter.permissions,
                "side_effect": adapter.side_effect,
                "network": adapter.network,
                "resources": record.get("resources", {}),
                "portable_adapter": adapter.model_dump(mode="json"),
                "implementation_hash": proof,
            }
            binding["fingerprint"] = content_hash(binding)
        else:
            if adapter.locus != "host_mediated":
                # External services still need an explicitly mediated implementation.
                continue
            proof = content_hash(
                [record["receipt_id"], record.get("scope_hash"), adapter.model_dump()]
            )
            binding = None
            if granted:
                try:
                    concrete = node.model_copy(
                        update={
                            "capability": adapter.tool,
                            "capability_version": adapter.provider_version,
                        }
                    )
                    binding = resolve_task(concrete, workspace, offline=offline)
                except FlowError:
                    continue
                if binding["provider"] != "desktop_host":
                    continue
        observations.append((adapter, binding, proof, granted))
    return observations, False


def _contract_matches(adapter, node, rule, offline):
    limits = rule.constraints
    if (
        adapter.capability != rule.capability
        or rule.version is not None
        and adapter.capability_version != rule.version
        or rule.execution_loci
        and adapter.locus not in rule.execution_loci
        or sys.platform not in adapter.platforms
        or adapter.side_effect != node.side_effect
        or set(adapter.permissions) != set(node.permissions)
        or (offline or limits.offline_only)
        and adapter.network
        or adapter.max_cost_micro_usd > limits.max_cost_micro_usd
        or limits.contract_hash
        and adapter.contract_hash != limits.contract_hash
        or limits.numeric_representation
        and adapter.numeric_representation != limits.numeric_representation
        or limits.max_latency_ms is not None
        and (adapter.max_latency_ms is None or adapter.max_latency_ms > limits.max_latency_ms)
        or limits.max_data_age_seconds is not None
        and (
            adapter.max_data_age_seconds is None
            or adapter.max_data_age_seconds > limits.max_data_age_seconds
        )
        or not set(limits.error_codes) <= set(adapter.error_codes)
        or adapter.inputs.keys() != node.inputs.keys()
        or adapter.outputs.keys() != node.outputs.keys()
    ):
        return False
    return all(
        compatible(binding.port, adapter.inputs[key]) for key, binding in node.inputs.items()
    ) and all(compatible(adapter.outputs[key], port) for key, port in node.outputs.items())


def automatic_probe(candidate, binding):
    if binding is None:
        return False
    resources = binding.get("resources") or {}
    return (
        candidate["allowed"]
        and candidate["locus"] == "local_runtime"
        and candidate["cost_micro_usd"] == 0
        and not candidate["permission_scope"]
        and binding["side_effect"] == "none"
        and binding["network"] is False
        and not resources.get("network")
        and not resources.get("model_calls")
    )


def replacement_candidates(key, node, workspace, *, offline=False):
    """Find observed implementations with the same declared ports and authority."""
    version = node.capability_version
    if version.isdecimal():
        version += ".0.0"
    try:
        rule = CapabilityRequirement(
            id=node.id, capability=node.capability, version=version, required=True, portability="flexible",
        )
    except ValidationError:
        return []
    decision, _ = resolve_requirement(key, node, rule, workspace, offline=offline)
    return [
        item for item in decision["candidates"]
        if (item["tool"], item["provider_version"]) != (node.capability, node.capability_version)
    ]


def resolve_requirement(key, node, rule, workspace, *, offline=False):
    candidates = []
    matches = {}
    built = _builtin(node, rule, workspace)
    observed, catalog_unavailable = _catalog_adapters(node, workspace, offline)
    if built:
        observed.insert(0, built)
    if rule.portability != "unresolved":
        for adapter, binding, evidence, granted in observed:
            if rule.portability == "pinned" and adapter.ref != rule.pinned_adapter:
                continue
            if not _contract_matches(adapter, node, rule, offline):
                continue
            descriptor = adapter.model_dump(mode="json")
            schema_hash = content_hash([descriptor["inputs"], descriptor["outputs"]])
            environment = content_hash([sys.platform, adapter.locus, evidence, descriptor])
            candidate = {
                "adapter": adapter.ref,
                "adapter_id": adapter.id,
                "tool": adapter.tool,
                "provider_version": adapter.provider_version,
                "model_version": adapter.model_version,
                "locus": adapter.locus,
                "schema_hash": schema_hash,
                "environment_fingerprint": environment,
                "implementation_hash": evidence,
                "permission_scope": sorted(adapter.permissions),
                "cost_micro_usd": adapter.max_cost_micro_usd,
                "contract_match": True,
                "behavioral_verdict": "NOT_RUN",
                "allowed": granted,
            }
            if adapter.ref in matches:
                raise FlowError("PATTERN_DEPENDENCY_CONFLICT", "環境清冊有重複的 Adapter 版本。")
            matches[adapter.ref] = (candidate, binding)
            candidates.append(candidate)
    candidates.sort(
        key=lambda row: (
            not row["allowed"],
            row["locus"] != "local_runtime",
            row["cost_micro_usd"],
            row["adapter"],
        )
    )
    if any(candidate["allowed"] for candidate in candidates):
        status, code = "needs_qualification", "BINDING_NEEDS_QUALIFICATION"
    elif candidates:
        status, code = "needs_permission", "PERMISSION_DENIED"
    elif rule.portability == "unresolved":
        status, code = "blocked", "CAPABILITY_UNAVAILABLE"
    elif rule.portability == "pinned":
        status, code = "needs_installation", "PINNED_DEPENDENCY_MISSING"
    elif not rule.required:
        status, code = "blocked", "OPTIONAL_DECISION_REQUIRED"
    else:
        status, code = "needs_installation", "CAPABILITY_UNAVAILABLE"
    return {
        "node": key,
        "requirement_id": rule.id,
        "capability": rule.capability,
        "capability_version": rule.version,
        "required": rule.required,
        "portability": rule.portability,
        "status": status,
        "code": code,
        "candidates": candidates,
        "automatic_candidates": [
            item["adapter"] for item in candidates if automatic_probe(item, matches[item["adapter"]][1])
        ],
        "installation_allowed": False,
        "catalog_unavailable": catalog_unavailable,
        "fallback_contract": rule.fallback_contract,
    }, matches
