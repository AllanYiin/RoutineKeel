from routinekeel_contracts.references import parse_reference


def predicate_value(predicate, value):
    if predicate.get("field") is not None:
        if not isinstance(value, dict) or predicate["field"] not in value:
            raise ValueError("條件欄位缺失")
        value = value[predicate["field"]]
        kinds = {"string": (str,), "number": (float, int), "integer": (int,), "boolean": (bool,)}
        if type(value) not in kinds[predicate["field_type"]]:
            raise ValueError("條件欄位的實際型別不符")
    operand = predicate["value"]
    if predicate["op"] == "equals":
        return value == operand
    if predicate["op"] == "less_than":
        return value < operand
    if predicate["op"] == "greater_than":
        return value > operand
    raise ValueError("不支援的條件運算子")


def gate_map(graph):
    gates = {}
    for node in graph["nodes"]:
        if node["kind"] == "switch":
            for branch, targets in node["routes"].items():
                for target in targets:
                    gates.setdefault(target, []).append((node["id"], branch))
    return gates


def readiness(node, state, parents, gates):
    records = state["nodes"]
    for switch, branch in gates.get(node["id"], []):
        parent = records[switch]
        if parent["status"] == "skipped" or (
            parent["status"] == "succeeded" and parent["selected_route"] != branch
        ):
            return "skip"
        if parent["status"] != "succeeded":
            return "wait"
    statuses = [records[p]["status"] for p in parents]
    if node["kind"] == "join":
        policy = node["policy"]
        if policy == "selected_branch":
            switch = records[node["switch"]]
            if switch["status"] == "skipped":
                return "skip"
            if switch["status"] != "succeeded":
                return "wait"
            return (
                "ready"
                if all(
                    records[parse_reference(ref).owner]["status"] == "succeeded"
                    for ref in node["branches"][switch["selected_route"]].values()
                )
                else "wait"
            )
        if statuses and all(v == "skipped" for v in statuses):
            return "skip"
        allowed = {"skipped", "succeeded"}
        if policy == "all_done":
            allowed.add("failed")
        return "ready" if all(v in allowed for v in statuses) else "wait"
    if "skipped" in statuses:
        return "skip"
    return "ready" if all(s == "succeeded" for s in statuses) else "wait"
