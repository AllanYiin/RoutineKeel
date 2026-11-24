"""Project a qualified hosted toolchain into its enclosing Graph session."""

from ..blueprints.sessions import refs
from ..blueprints.source import require
from ..runtime import digest, read_json
from .iterations import merged_meter, mirror


def run_host_chain(session, state, node, binding, values):
    from .session import GraphSession

    key = node["id"]
    record = state["nodes"][key]
    plan = binding["chain_plan"]
    identity = digest([session.id, key, record["attempt"], record["inputs_hash"], digest(plan)])
    entry = {"id": identity, "child_session": "job_" + identity[:32]}
    if "chain" not in record:
        record["chain"] = entry
        session.checkpoint(state, key, "toolchain_started", child_session=entry["child_session"])
    require(record["chain"]["id"] == identity, "工具鏈的工作輸入或版本已變更。")
    child = GraphSession(session.runtime, entry["child_session"])
    if child.path.is_file():
        snapshot = read_json(child.path)
        require(
            snapshot.get("owner_session") == session.id and snapshot.get("owner_node") == key,
            "工具鏈子工作不屬於本次節點。",
        )
        view = child.interact("next", {})
    else:
        view = GraphSession.begin(
            session.runtime,
            plan,
            {"inputs": values},
            session.runtime.workspace_root,
            binding["chain_bindings"],
            key=identity[:32],
            owner_session=session.id,
            owner_node=key,
            known_assets=set(refs(values)),
            offline=state.get("offline", False),
        )
    children = view["nodes"]
    record["chain"].update(
        external_claim=any(child.get("external_claim") for child in children.values()),
        spent=merged_meter(children),
    )
    if view["pending_tasks"]:
        return mirror(session, state, node, entry, view)
    if view["status"] != "complete":
        record.update(status="failed", error="CHILD_FAILED")
        session.checkpoint(state, key, "toolchain_failed", child_session=entry["child_session"])
        return None
    return session.complete_node(
        state,
        node,
        view["outputs"],
        {
            "kind": "toolchain_execution",
            "chain_graph_hash": plan["graph_hash"],
            "qualification_receipt": binding["qualification_receipt"],
        },
    )
