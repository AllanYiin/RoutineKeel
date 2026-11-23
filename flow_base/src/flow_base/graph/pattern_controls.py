"""Close portable control ports with observable, fixture-only Graph steps."""

from routinekeel_contracts.domain_assets import content_hash
from routinekeel_contracts.errors import StudioError
from routinekeel_contracts.references import parse_reference

FALLBACK_FILE = "fixtures/control-fallback.txt"
_FALLBACK_VALUES = {
    "string": "",
    "integer": 0,
    "number": 0,
    "boolean": False,
    "object": {},
    "array": [],
    "file": {"$file": FALLBACK_FILE},
    "json": None,
    "null": None,
}


def close_controls(graph, boundary, cases):
    if not (boundary.control_inputs or boundary.control_outputs):
        return cases, None
    nodes = {node["id"]: node for node in graph["nodes"]}
    inputs = graph["inputs"]
    injected, routes, edges = {}, {}, {}
    terminal = []

    def name(label):
        key = "fixture_" + content_hash(label)[:24]
        if key in nodes or key in inputs:
            raise StudioError(
                "PATTERN_GRAPH_INVALID", "Pattern 與控制測具的識別碼衝突。"
            )
        return key

    def barrier(label, depends=()):
        key = name(label)
        nodes[key] = {
            "id": key,
            "kind": "join",
            "policy": "all_success",
            "depends_on": list(depends),
        }
        return key

    for port_name, port in boundary.control_inputs.items():
        if port.kind == "order":
            entry = barrier(["control_input", port_name])
            for node in port.nodes:
                nodes[node].setdefault("depends_on", []).append(entry)
            edges[f"input/{port_name}"] = entry
            terminal.append(entry)
            continue
        selector = name(["selector", port_name])
        key = name(["switch", port_name])
        branch_labels = ["true", "default" if port.route == "default" else "false"]
        condition = {"source": f"input://{selector}", "op": "equals", "value": True}
        if port.route == "error":
            branch_labels.append("error")
            inputs[selector] = {"type": "object"}
            injected[selector] = {}
            condition.update(field="selected", field_type="boolean")
        else:
            inputs[selector] = {"type": "boolean"}
            injected[selector] = port.route == "true"
        targets = {}
        for label in branch_labels:
            witness = barrier(["input_route", port_name, label])
            terminal.append(witness)
            targets[label] = (
                [witness, *port.nodes] if label == port.route else [witness]
            )
            if label == port.route:
                edges[f"input/{port_name}"] = witness
        nodes[key] = {
            "id": key,
            "kind": "switch",
            "condition": condition,
            "routes": targets,
            "on_error": "route" if port.route == "error" else "failed",
        }
        routes[key] = port.route
    for port_name, port in boundary.control_outputs.items():
        if port.kind == "order":
            key = barrier(["control_output", port_name], port.nodes)
            terminal.append(key)
            edges[f"output/{port_name}"] = key
        else:
            for source in port.nodes:
                node = nodes[source]
                if node["kind"] != "switch" or port.route not in node["routes"]:
                    raise StudioError(
                        "PATTERN_GRAPH_INVALID", "控制輸出須來自原標籤的選路節點。"
                    )
                key = barrier(["control_output", port_name, source])
                node["routes"][port.route].append(key)
                terminal.append(key)
                edges[f"output/{port_name}/{source}"] = key

    delivery = name(["delivery"])
    outputs = graph["outputs"]
    fallbacks = {}
    ports = {}
    for key, source in outputs.items():
        ref = parse_reference(source)
        port = nodes[ref.owner]["outputs"][ref.port]
        fallback = name(["fallback", key])
        inputs[fallback] = port
        injected[fallback] = _FALLBACK_VALUES[port["type"]]
        fallbacks[key] = f"input://{fallback}"
        ports[key] = {"source": source, "port": port}
    nodes[delivery] = {
        "id": delivery,
        "kind": "join",
        "policy": "all_done",
        "depends_on": terminal,
        "inputs": ports,
        "outputs": {key: port["port"] for key, port in ports.items()},
        "fallbacks": fallbacks,
    }
    graph["nodes"] = list(nodes.values())
    graph["outputs"] = {key: f"artifact://{delivery}/{key}" for key in outputs}
    augmented = [{**case, "inputs": {**case["inputs"], **injected}} for case in cases]
    return augmented, {"delivery": delivery, "routes": routes, "edges": edges}


def verify_controls(contract, observations):
    by_step = {row["step"]: row for row in observations}
    delivery = by_step.get(contract["delivery"], {})
    if delivery.get("fallback_used") != [] or any(
        by_step.get(step, {}).get("selected_route") != route
        for step, route in contract["routes"].items()
    ):
        raise StudioError(
            "TEST_FAILED", "控制試做須實際走完指定路由且交付原 Pattern 產物。"
        )
    return sorted(
        port for port, witness in contract["edges"].items() if witness in by_step
    )
