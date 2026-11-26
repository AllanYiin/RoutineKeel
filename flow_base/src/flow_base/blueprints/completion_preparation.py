"""Use trusted preparation recipes for otherwise compatible missing adapters."""

from functools import partial

from pydantic import ValidationError
from routinekeel_contracts.bindings import ImplementationAdapter
from routinekeel_contracts.graph_validator import validate_blueprint
from routinekeel_contracts.pattern_composition import (
    graph_requirements,
    graph_task_paths,
)

from ..graph.capabilities import capability_observation, host_inventory_path
from ..graph.portable import _contract_rejections
from ..graph.preparation import (
    adapter_recipe,
    load_recipe,
    preparation_id,
    prepare_recipe,
    read_preparation,
)
from ..graph.toolchain_plan import observed_tools, search_toolchains
from ..runtime import FlowError
from .binding_operations import BindingOperation, BindingOperations


def preparation_candidates(task, rule, rejections, catalog, offline):
    """Only list verified host grants for recipes compatible with this node."""
    candidates = {}
    for item in rejections:
        if item["code"] != "DEPENDENCY_NOT_INSTALLED" or not item.get("recipe_id"):
            continue
        record = catalog.get(item["tool"], {})
        if record.get("allowed") is not True or record.get("preparation_allowed") is not True:
            continue
        try:
            adapter = ImplementationAdapter.model_validate(record.get("portable"))
            recipe = adapter_recipe(record, adapter)
        except (ValidationError, FlowError):
            continue
        if rule.portability == "pinned" and rule.pinned_adapter != adapter.ref:
            continue
        if recipe is not None and not _contract_rejections(adapter, task, rule, offline, record.get("destination")):
            candidates[preparation_id(recipe)] = (adapter, recipe)
    return candidates


def _prepare_authorized(workspace, configuration, approved, recipe, offline):
    """Recheck the launcher grant after acquiring a slot and before preparing any files."""
    current_path = host_inventory_path(workspace.root)
    catalog, observation = capability_observation(workspace.root)
    record = catalog.get(recipe.tool, {})
    if (observation["status"] != "current" or current_path is None
            or current_path.resolve() != configuration or record.get("allowed") is not True
            or record.get("preparation_allowed") is not True
            or any(record.get(key) != approved.get(key) for key in (
                "portable", "preparation", "destination", "receipt_id", "version", "inputs", "outputs",
                "network", "side_effect", "permissions", "platforms", "resources",
            ))):
        raise FlowError("PREPARATION_AUTHORIZATION_CHANGED", "安裝配方、來源或授權已變更，請沿原工作重查。")
    download_allowed = approved.get("download_allowed") is True and record.get("download_allowed") is True
    return prepare_recipe(workspace.runtime, recipe, configuration.parent,
                          download_allowed=download_allowed, offline=offline)


def prepare_missing_adapters(service, state, checkpoint, *, retry=False):
    workspace = service.workspace
    graph, _ = validate_blueprint(
        service.install_workflow(draft_id=state["draft_id"])["source"]["graph"]
    )
    tasks, rules = graph_task_paths(graph), graph_requirements(graph)
    catalog, observation = capability_observation(workspace.root)
    if observation["status"] != "current":
        return state
    configuration = host_inventory_path(workspace.root)
    results = dict(state.get("preparations", {}))
    if configuration is None:
        return state
    configuration = configuration.resolve()
    jobs = {}
    planned = []
    for gap in state["gaps"]:
        node = gap["location"]
        choice = state.get("binding_selections", {}).get(node, {})
        requested = choice.get("preparation_id")
        if (
            gap["kind"] != "capability"
            or node not in rules
            or rules[node].portability == "unresolved"
            or not requested and any(item["allowed"] for item in gap["candidates"])
        ):
            continue
        eligible = preparation_candidates(tasks[node], rules[node], gap.get("candidate_rejections", []),
                                          catalog, state["offline"])
        if requested:
            selected = eligible.get(requested)
            if selected is None or selected[0].ref != choice.get("adapter"):
                continue
            candidates = {requested: selected[1]}
        elif not eligible:
            candidates = _prerequisites(
                tasks[node], rules[node], workspace.root, catalog, state["offline"]
            )
        elif len(eligible) > 1:
            continue
        else:
            candidates = {op_id: recipe for op_id, (_, recipe) in eligible.items()}
        for op_id, recipe in sorted(candidates.items()):
            verified = results.get(node, {}).get("verified_operations", {})
            if op_id in verified:
                try:
                    if read_preparation(workspace.runtime, recipe) == verified[op_id]:
                        continue
                except FlowError:
                    pass
                verified = {key: receipt for key, receipt in verified.items() if key != op_id}
            results[node] = {
                "operation_id": op_id,
                "recipe_id": recipe.id,
                "status": "preparing",
                "verified_operations": verified,
            }
            operation = BindingOperation("prepare", op_id)
            jobs[operation.id] = (
                operation, partial(_prepare_authorized, workspace, configuration, catalog[recipe.tool], recipe, state["offline"]),
                lambda _receipt: True,
            )
            planned.append((node, op_id, recipe, operation.id))
    if jobs:
        store = BindingOperations(workspace.runtime, state["work_id"])
        checkpoint({**state, "status": "preparing", "preparations": dict(results)})
        store.register([job[0] for job in jobs.values()])
        for _node, _op_id, recipe, operation_id in planned:
            try:
                previous = read_preparation(workspace.runtime, recipe)
            except FlowError:
                previous = None
            if previous is None:
                store.invalidate(operation_id)
        prepared = store.run(list(jobs.values()), retry_failed=retry)
        for node, op_id, recipe, operation_id in planned:
            receipt = prepared.get(operation_id)
            if receipt is None:
                continue
            outcome = receipt.get("operation_error")
            if outcome:
                results[node] = {
                    **results[node],
                    "status": "blocked",
                    "operation_id": op_id,
                    "recipe_id": recipe.id,
                    "code": outcome["code"],
                    "reason": outcome["message"],
                    **({"diagnostic": outcome["details"]} if outcome["details"] else {}),
                }
            else:
                previous = results[node]
                verified = {**previous.get("verified_operations", {}), op_id: receipt}
                if previous["status"] == "blocked":
                    results[node] = {**previous, "verified_operations": verified}
                else:
                    results[node] = {
                        "operation_id": op_id, "recipe_id": recipe.id, "status": "prepared",
                        "receipt": receipt, "verified_operations": verified,
                    }
        checkpoint({**state, "status": "needs_input", "preparations": dict(results)})
    return {**state, "preparations": results} if results != state.get("preparations", {}) else state


def _prerequisites(task, rule, workspace, catalog, offline):
    operations = list(observed_tools(workspace, catalog, include_pending=True))
    pending = {
        (item.adapter.ref, item.proof): load_recipe(item.binding["preparation"])
        for item in operations
        if item.binding.get("preparation_pending")
    }
    if not pending:
        return {}
    planning = search_toolchains(task, rule, operations, offline=offline)
    if planning["status"] != "proposed":
        return {}
    candidates = {}
    for plan in planning["plans"]:
        recipes = [
            pending[(step["adapter"], step["implementation_hash"])]
            for step in plan["steps"]
            if (step["adapter"], step["implementation_hash"]) in pending
        ]
        selected = {preparation_id(recipe): recipe for recipe in recipes}
        candidates[tuple(sorted(selected))] = selected
    return next(iter(candidates.values())) if len(candidates) == 1 else {}
