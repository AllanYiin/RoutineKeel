"""Resolve portable requirements from observed local and desktop tool inventories."""

import sys
from hashlib import sha256
from importlib.metadata import entry_points

from pydantic import ValidationError
from routinekeel_contracts.bindings import ImplementationAdapter
from routinekeel_contracts.domain_assets import CapabilityRequirement, content_hash
from routinekeel_contracts.graph_validator import compatible
from routinekeel_contracts.pattern_composition import graph_requirements  # noqa: F401

from ..runtime import FlowError

ADAPTER_GROUP = "routinekeel.portable_adapters"


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


def _catalog_adapters(node, workspace, inventory, *, include_pending=False):
    from ..workspace import Workspace
    from .capabilities import resolve_task
    from .preparation import adapter_recipe, read_preparation

    observations = []
    rejected = []
    for name, record in sorted(inventory.items()):
        if not isinstance(record, dict) or "portable" not in record:
            continue
        identity = {"tool": name, "source_id": record.get("source_id")}
        resources = record.get("resources") or {}
        try:
            adapter = ImplementationAdapter.model_validate(record["portable"])
        except ValidationError:
            rejected.append({**identity, "code": "INVALID_ADAPTER_DESCRIPTOR", "field": "portable"})
            continue
        identity["adapter"] = adapter.ref
        permissions = record.get("permissions", [])
        platforms = record.get("platforms", [])
        if (
            not record.get("receipt_id")
            or adapter.tool != name
            or adapter.provider_version != record.get("version")
            or not isinstance(permissions, list)
            or not all(isinstance(permission, str) for permission in permissions)
            or set(adapter.permissions) != set(permissions)
            or adapter.side_effect != record.get("side_effect")
            or adapter.network != record.get("network")
            or not isinstance(resources, dict)
            or adapter.max_cost_micro_usd != resources.get("cost_micro_usd", 0)
        ):
            rejected.append({**identity, "code": "OBSERVATION_MISMATCH", "field": "receipt"})
            continue
        if record.get("available") is not True:
            rejected.append({**identity, "code": "TOOL_UNAVAILABLE", "field": "available"})
            continue
        if not isinstance(platforms, list) or sys.platform not in platforms:
            rejected.append({**identity, "code": "PLATFORM_MISMATCH", "field": "platforms"})
            continue
        granted = record.get("allowed") is True
        if adapter.locus == "local_runtime":
            try:
                recipe = adapter_recipe(record, adapter)
                receipt = read_preparation(Workspace(workspace).runtime, recipe) if recipe else None
            except FlowError as error:
                rejected.append({**identity, "code": error.code, "field": "preparation"})
                continue
            installed = local_entry(adapter) if recipe is None else None
            pending = False
            if not receipt and not installed:
                rejected.append(
                    {
                        **identity,
                        "code": "DEPENDENCY_NOT_INSTALLED",
                        "field": "distribution",
                        **({"recipe_id": recipe.id} if recipe else {}),
                    }
                )
                pending = bool(
                    include_pending
                    and recipe is not None
                    and record.get("preparation_allowed") is True
                )
                if not pending:
                    continue
            if pending:
                proof = content_hash(["pending", recipe.model_dump(mode="json")])
            else:
                proof = content_hash(receipt) if receipt else installed[1]
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
            if recipe is not None:
                binding["preparation"] = recipe.model_dump(mode="json")
            if pending:
                binding["preparation_pending"] = True
            binding["fingerprint"] = content_hash(binding)
        else:
            if adapter.locus != "host_mediated":
                rejected.append({**identity, "code": "HOST_MEDIATION_REQUIRED", "field": "locus"})
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
                    binding = resolve_task(concrete, workspace, catalog=inventory)
                except FlowError as error:
                    rejected.append({**identity, "code": error.code, "field": "binding"})
                    continue
                if binding["provider"] != "desktop_host":
                    rejected.append(
                        {**identity, "code": "HOST_MEDIATION_REQUIRED", "field": "provider"}
                    )
                    continue
        observations.append((adapter, binding, proof, granted, record.get("destination"), identity))
    return observations, rejected


def _contract_rejections(adapter, node, rule, offline, destination):
    limits = rule.constraints
    checks = (
        ("CAPABILITY_MISMATCH", "capability", adapter.capability == rule.capability),
        ("VERSION_MISMATCH", "version", rule.accepts_version(adapter.capability_version)),
        (
            "LOCUS_MISMATCH",
            "execution_loci",
            not rule.execution_loci or adapter.locus in rule.execution_loci,
        ),
        ("PLATFORM_MISMATCH", "platforms", sys.platform in adapter.platforms),
        ("SIDE_EFFECT_MISMATCH", "side_effect", adapter.side_effect == node.side_effect),
        (
            "PERMISSION_SCOPE_MISMATCH",
            "permissions",
            set(adapter.permissions) == set(node.permissions),
        ),
        (
            "OFFLINE_REQUIRED",
            "network",
            not (offline or limits.offline_only) or not adapter.network,
        ),
        (
            "COST_LIMIT",
            "max_cost_micro_usd",
            adapter.max_cost_micro_usd <= limits.max_cost_micro_usd,
        ),
        (
            "CONTRACT_HASH_MISMATCH",
            "contract_hash",
            limits.contract_hash in {None, adapter.contract_hash},
        ),
        (
            "NUMERIC_REPRESENTATION_MISMATCH",
            "numeric_representation",
            limits.numeric_representation in {None, adapter.numeric_representation},
        ),
        (
            "LATENCY_LIMIT",
            "max_latency_ms",
            limits.max_latency_ms is None
            or (
                adapter.max_latency_ms is not None
                and adapter.max_latency_ms <= limits.max_latency_ms
            ),
        ),
        (
            "FRESHNESS_LIMIT",
            "max_data_age_seconds",
            limits.max_data_age_seconds is None
            or (
                adapter.max_data_age_seconds is not None
                and adapter.max_data_age_seconds <= limits.max_data_age_seconds
            ),
        ),
        (
            "ERROR_CONTRACT_MISMATCH",
            "error_codes",
            set(limits.error_codes) <= set(adapter.error_codes),
        ),
        ("INPUT_PORTS_MISMATCH", "inputs", adapter.inputs.keys() == node.inputs.keys()),
        ("OUTPUT_PORTS_MISMATCH", "outputs", adapter.outputs.keys() == node.outputs.keys()),
    )
    rejected = [(code, field) for code, field, matches in checks if not matches]
    for key in node.inputs.keys() & adapter.inputs.keys():
        if not compatible(node.inputs[key].port, adapter.inputs[key]):
            rejected.append(("INPUT_TYPE_MISMATCH", "inputs/" + key))
    for key in node.outputs.keys() & adapter.outputs.keys():
        if not compatible(adapter.outputs[key], node.outputs[key]):
            rejected.append(("OUTPUT_TYPE_MISMATCH", "outputs/" + key))
    for field, ports, semantics in (
        ("inputs", adapter.inputs, rule.input_semantics),
        ("outputs", adapter.outputs, rule.output_semantics),
    ):
        for key, expected in semantics.items():
            if key not in ports or ports[key].schema_id != expected:
                rejected.append(("PORT_SEMANTICS_MISMATCH", field + "/" + key))
    if rule.allowed_destinations and destination not in rule.allowed_destinations:
        rejected.append(("DESTINATION_NOT_ALLOWED", "destination"))
    return sorted(set(rejected))


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


def replacement_decision(key, node, workspace, *, offline=False):
    """Diagnose replacements for a tool that was fixed by the source blueprint."""
    version = node.capability_version
    if version.isdecimal():
        version += ".0.0"
    try:
        rule = CapabilityRequirement(
            id=node.id,
            capability=node.capability,
            version=version,
            required=True,
            portability="flexible",
        )
    except ValidationError:
        return {"candidates": [], "rejections": []}
    decision, _ = resolve_requirement(key, node, rule, workspace, offline=offline)
    decision["candidates"] = [
        item
        for item in decision["candidates"]
        if (item["tool"], item["provider_version"]) != (node.capability, node.capability_version)
    ]
    return decision


def resolve_requirement(
    key,
    node,
    rule,
    workspace,
    *,
    offline=False,
    observation=None,
    workflow=None,
    selected_plan=None,
    planning_search=None,
):
    from .capabilities import capability_observation

    candidates = []
    matches = {}
    built = _builtin(node, rule, workspace)
    inventory, snapshot = capability_observation(workspace) if observation is None else observation
    observed, rejected = _catalog_adapters(node, workspace, inventory)
    if built:
        adapter = built[0]
        observed.insert(0, (*built, None, {"tool": adapter.tool, "adapter": adapter.ref}))
    if rule.portability != "unresolved":
        for adapter, binding, evidence, granted, destination, identity in observed:
            if rule.portability == "pinned" and adapter.ref != rule.pinned_adapter:
                rejected.append({**identity, "code": "PINNED_ADAPTER_MISMATCH", "field": "adapter"})
                continue
            failures = _contract_rejections(adapter, node, rule, offline, destination)
            if failures:
                rejected.extend(
                    {**identity, "code": code, "field": field} for code, field in failures
                )
                continue
            descriptor = adapter.model_dump(mode="json")
            schema_hash = content_hash([descriptor["inputs"], descriptor["outputs"]])
            extended = node.capability_requirement is not None or bool(
                rule.version_range or rule.allowed_destinations
            )
            scope = [sys.platform, adapter.locus, evidence, descriptor]
            if extended:
                scope.append(destination)
            environment = content_hash(scope)
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
            if extended:
                candidate["capability_version"] = adapter.capability_version
            if not granted:
                rejected.append({**identity, "code": "PERMISSION_DENIED", "field": "authorization"})
            if adapter.ref in matches:
                raise FlowError("PATTERN_DEPENDENCY_CONFLICT", "環境清冊有重複的 Adapter 版本。")
            matches[adapter.ref] = (candidate, binding)
            candidates.append(candidate)
    planning = None
    if workflow is not None and (
        selected_plan is not None
        or rule.portability == "flexible"
        and not any(candidate["allowed"] for candidate in candidates)
    ):
        from routinekeel_contracts.errors import StudioError

        from .toolchain_plan import chain_candidate, observed_tools, search_toolchains

        operations = list(observed_tools(workspace, inventory))
        if selected_plan is not None:
            result = {"status": "proposed", "plans": [selected_plan], "evaluated": 0}
        elif planning_search is not None:
            result = planning_search(key, node, rule, operations, snapshot, offline=offline)
        else:
            result = search_toolchains(node, rule, operations, offline=offline)
        compiled, failures = [], []
        for plan in result["plans"]:
            try:
                candidate, binding = chain_candidate(
                    node,
                    rule,
                    plan,
                    operations,
                    workflow,
                    workspace,
                    offline=offline,
                )
                if candidate["adapter"] in matches:
                    raise FlowError(
                        "PATTERN_DEPENDENCY_CONFLICT", "工具鏈與現有 Adapter 識別重複。"
                    )
            except (FlowError, StudioError) as error:
                failures.append(
                    {"plan_id": plan["plan_id"], "code": error.code, "reason": str(error)}
                )
                continue
            compiled.append(candidate["toolchain_plan"])
            candidates.append(candidate)
            matches[candidate["adapter"]] = (candidate, binding)
        planning = {**result, "plans": compiled, "observation_id": snapshot["id"]}
        if compiled and result["status"] == "no_chain":
            planning["status"] = "proposed"
        if failures:
            planning["rejections"] = failures
            if result["status"] == "proposed" and not compiled:
                planning["status"] = "compile_blocked"
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
    elif node.capability_requirement is not None and snapshot["status"] == "current":
        code = (
            "CAPABILITY_UNAVAILABLE"
            if any(row["code"] == "DEPENDENCY_NOT_INSTALLED" for row in rejected)
            else "NO_CANDIDATE"
        )
        status = "needs_installation" if code == "CAPABILITY_UNAVAILABLE" else "blocked"
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
            item["adapter"]
            for item in candidates
            if automatic_probe(item, matches[item["adapter"]][1])
        ],
        "installation_allowed": False,
        "catalog_unavailable": snapshot["status"] != "current",
        "observation": snapshot,
        "rejections": rejected,
        "fallback_contract": rule.fallback_contract,
        **({"toolchain_planning": planning} if planning is not None else {}),
    }, matches
