"""Use trusted preparation recipes for otherwise compatible missing adapters."""

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
)
from ..graph.toolchain_plan import observed_tools, search_toolchains
from ..runtime import FlowError


def prepare_missing_adapters(service, state, checkpoint):
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
    for gap in state["gaps"]:
        node = gap["location"]
        if (
            gap["kind"] != "capability"
            or node not in rules
            or rules[node].portability == "unresolved"
            or any(item["allowed"] for item in gap["candidates"])
        ):
            continue
        candidates = {}
        for item in gap.get("candidate_rejections", []):
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
            if rules[node].portability == "pinned" and rules[node].pinned_adapter != adapter.ref:
                continue
            if recipe is None or _contract_rejections(
                adapter,
                tasks[node],
                rules[node],
                state["offline"],
                record.get("destination"),
            ):
                continue
            candidates[preparation_id(recipe)] = recipe
        if not candidates:
            candidates = _prerequisites(
                tasks[node], rules[node], workspace.root, catalog, state["offline"]
            )
        elif len(candidates) > 1:
            continue
        for op_id, recipe in sorted(candidates.items()):
            verified = results.get(node, {}).get("verified_operations", {})
            if op_id in verified:
                continue
            results[node] = {
                "operation_id": op_id,
                "recipe_id": recipe.id,
                "status": "preparing",
                "verified_operations": verified,
            }
            checkpoint({**state, "status": "preparing", "preparations": dict(results)})
            try:
                receipt = prepare_recipe(
                    workspace.runtime, recipe, configuration.resolve().parent
                )
            except (FlowError, OSError, ValueError) as exc:
                results[node] = {
                    **results[node],
                    "status": "blocked",
                    "code": getattr(exc, "code", "TOOLCHAIN_PREPARATION_BLOCKED"),
                    "reason": str(exc)
                    if isinstance(exc, FlowError)
                    else "無法安全讀取或準備離線套件。",
                }
                checkpoint({**state, "status": "needs_input", "preparations": dict(results)})
                break
            results[node] = {
                **results[node],
                "status": "prepared",
                "receipt": receipt,
                "verified_operations": {**verified, op_id: receipt},
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
