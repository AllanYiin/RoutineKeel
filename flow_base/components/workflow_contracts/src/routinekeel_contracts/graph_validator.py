"""Static data and control-flow validation for the non-executable blueprint."""

from pydantic import ValidationError

from .builder import layers
from .errors import StudioError
from .graph import Blueprint, ForEach, GraphPort, Join, Repeat, Switch
from .references import parse_reference


def require(ok, message):
    if not ok:
        raise StudioError("GRAPH_INVALID", message)


def reference(text):
    try:
        ref = parse_reference(text)
    except StudioError as exc:
        raise StudioError("GRAPH_INVALID", "資料引用格式不符") from exc
    require(ref.scheme in {"input", "artifact"}, "只接受 input 或 artifact 引用")
    return ref


def compatible(actual, expected):
    return (
        (
            actual.type == expected.type
            or expected.type == "json"
            or (actual.type, expected.type) == ("integer", "number")
        )
        and (not expected.format or actual.format == expected.format)
        and (not expected.required or actual.required)
        and (
            expected.items is None
            or (actual.items is not None and compatible(actual.items, expected.items))
        )
    )


def port_for(graph, nodes, value):
    ref = reference(value)
    ports = (
        graph.inputs
        if ref.scheme == "input"
        else nodes[ref.owner].outputs
        if ref.owner in nodes
        else {}
    )
    name = ref.owner if ref.scheme == "input" else ref.port
    require(name in ports, f"找不到來源埠 {value}")
    return ports[name]


def check_predicate(predicate, port):
    require((predicate.field is None) == (predicate.field_type is None), "欄位條件須宣告欄位與型別")
    if predicate.field:
        require(port.type == "object", "欄位條件需要物件來源")
        port = GraphPort(type=predicate.field_type)
    operand = {bool: "boolean", str: "string", int: "integer", float: "number"}[
        type(predicate.value)
    ]
    numeric = port.type in {"number", "integer"} and operand in {"number", "integer"}
    require(port.required and (numeric or operand == port.type), "條件運算元型別不符")
    require(predicate.op == "equals" or numeric, "大小比較需要數值埠")


def validate_structure(
    graph, depth=0, *, max_nodes=200, dependencies=None, route_guards=None, extended=False
):
    require(depth <= 8, "子圖巢狀層數超過限制")
    require(len(graph.nodes) <= max_nodes, "此版本的 Graph 節點數超過上限")
    nodes = {n.id: n for n in graph.nodes}
    require(len(nodes) == len(graph.nodes), "節點 ID 重複")
    deps = {n.id: set(n.depends_on) for n in graph.nodes}
    routes = {n.id: {} for n in graph.nodes}
    for node in graph.nodes:
        refs = [b.source for b in node.inputs.values()]
        require(len(set(node.depends_on)) == len(node.depends_on), "重複的順序依賴")
        for binding in node.inputs.values():
            require(
                compatible(port_for(graph, nodes, binding.source), binding.port), "輸入型別不相容"
            )
        if isinstance(node, Switch):
            labels = set(node.routes)
            require(
                extended or (node.condition.field is None and labels == {"true", "false"}),
                "延伸分支需要 Graph 2.2",
            )
            require(("error" in labels) == (node.on_error == "route"), "錯誤分支須有對應出口")
            require(
                not node.outputs and labels - {"error"} in ({"true", "false"}, {"true", "default"}),
                "條件須有完整出口",
            )
            refs.append(node.condition.source)
            check_predicate(node.condition, port_for(graph, nodes, node.condition.source))
            for label, targets in node.routes.items():
                require(targets, "條件出口缺少目標")
                for target in targets:
                    require(target in nodes and node.id not in routes[target], "條件目標未知或重疊")
                    routes[target][node.id] = label
                    deps[target].add(node.id)
        if isinstance(node, Join):
            if node.policy == "selected_branch":
                require(not node.fallbacks, "選路匯合須由分支提供產物")
                switch = nodes.get(node.switch)
                require(
                    isinstance(switch, Switch) and set(node.branches) == set(switch.routes),
                    "匯合出口不完整",
                )
                require(not node.inputs and node.outputs, "選路匯合須以各分支明確綁定輸出")
                deps[node.id].add(switch.id)
                for mapping in node.branches.values():
                    require(mapping.keys() == node.outputs.keys(), "分支交付埠不一致")
                    for name, value in mapping.items():
                        require(
                            compatible(port_for(graph, nodes, value), node.outputs[name]),
                            "分支輸出型別不符",
                        )
                        refs.append(value)
            else:
                require(not node.branches and node.switch is None, "普通匯合不能指定分支")
                if node.policy == "all_done":
                    require(
                        extended or not (node.inputs or node.outputs),
                        "帶備援交付的 all_done 需要 Graph 2.2",
                    )
                    require(
                        node.inputs.keys() == node.outputs.keys() == node.fallbacks.keys(),
                        "all_done 各交付須有明確備援",
                    )
                    for name, value in node.fallbacks.items():
                        require(
                            reference(value).scheme == "input"
                            and compatible(port_for(graph, nodes, value), node.outputs[name])
                            and compatible(node.inputs[name].port, node.outputs[name]),
                            "備援須為可用且相容的輸入",
                        )
                else:
                    require(not node.fallbacks, "all_success 不使用失敗備援")
                    require(node.inputs.keys() == node.outputs.keys(), "匯合交付須有對應輸入")
                    for name, binding in node.inputs.items():
                        require(compatible(binding.port, node.outputs[name]), "匯合轉送型別不符")
        if isinstance(node, (ForEach, Repeat)):
            body = node.body
            validate_structure(body, depth + 1, max_nodes=max_nodes, extended=extended)
            body_nodes = {n.id: n for n in body.nodes}
            if isinstance(node, ForEach):
                refs.append(node.items)
                items = port_for(graph, nodes, node.items)
                require(
                    items.required and items.type == "array" and items.items is not None,
                    "逐項來源須為有元素型別的陣列",
                )
                require(
                    set(body.inputs) == {"item"} and set(body.outputs) == {"result"},
                    "逐項子圖須宣告 item 與 result",
                )
                require(compatible(items.items, body.inputs["item"]), "逐項輸入元素型別不符")
                results = node.outputs.get("results")
                require(
                    set(node.outputs) == {"results"}
                    and results.type == "array"
                    and results.items is not None,
                    "逐項輸出須為有元素型別的 results 陣列",
                )
                require(
                    results.items.type == "object"
                    if node.on_item_failure == "collect_errors"
                    else compatible(
                        port_for(body, body_nodes, body.outputs["result"]), results.items
                    ),
                    "逐項交付型別不符",
                )
            else:
                refs.append(node.initial.source)
                require(
                    set(body.inputs) == {"value"} and "value" in body.outputs,
                    "重做子圖須宣告 value",
                )
                state = body.inputs["value"]
                require(
                    compatible(port_for(graph, nodes, node.initial.source), node.initial.port)
                    and compatible(node.initial.port, state),
                    "起始狀態型別不符",
                )
                output = port_for(body, body_nodes, body.outputs["value"])
                require(
                    compatible(output, state)
                    and set(node.outputs) == {"value"}
                    and compatible(output, node.outputs["value"]),
                    "迭代狀態無法接續",
                )
                require(node.condition.source in body.outputs.values(), "迭代條件須來自子圖交付物")
                check_predicate(node.condition, port_for(body, body_nodes, node.condition.source))
        for value in refs:
            port_for(graph, nodes, value)
            ref = reference(value)
            if ref.scheme == "artifact":
                deps[node.id].add(ref.owner)
        require(deps[node.id] <= nodes.keys(), "依賴不存在的節點")
    order = []
    guards = {}
    for ready in layers(deps):
        for key in ready:
            node = nodes[key]
            selected = isinstance(node, Join) and node.policy == "selected_branch"
            expected = dict(routes[key])
            if selected:
                switch_context = guards[node.switch]
                for label, mapping in node.branches.items():
                    for value in mapping.values():
                        ref = reference(value)
                        require(
                            ref.scheme == "artifact"
                            and guards[ref.owner] == {**switch_context, node.switch: label},
                            "分支產物無法保證存在",
                        )
                require(set(node.depends_on) <= {node.switch}, "選路匯合的額外前置條件不明確")
                expected.update(switch_context)
            elif isinstance(node, Join) and deps[key]:
                # An order barrier sees skipped nodes as terminal. Only guards
                # shared by all predecessors necessarily apply after the join.
                contexts = [set(guards[parent].items()) for parent in deps[key]]
                for switch_id, label in set.intersection(*contexts):
                    require(expected.setdefault(switch_id, label) == label, "匯合的控制路徑互斥")
                for binding in node.inputs.values():
                    ref = reference(binding.source)
                    if ref.scheme == "artifact":
                        require(
                            node.policy == "all_done"
                            or guards[ref.owner].items() <= expected.items(),
                            "匯合不能讀取未選分支",
                        )
            else:
                for parent in sorted(deps[key]):
                    for switch_id, label in guards[parent].items():
                        require(
                            expected.setdefault(switch_id, label) == label, "節點同時依賴互斥路徑"
                        )
            guards[key] = expected
        order.extend(ready)
    contributing = set()
    for value in graph.outputs.values():
        ref = reference(value)
        port_for(graph, nodes, value)
        require(
            ref.scheme == "artifact" and not guards[ref.owner], "最終交付須在所有成功路徑可取得"
        )
        contributing.add(ref.owner)
    for key in reversed(order):
        if key in contributing:
            contributing.update(deps[key])
    require(contributing == nodes.keys(), "有節點無法到達具名交付")
    graph.nodes = [nodes[key] for key in order]
    if dependencies is not None:
        dependencies.update({key: sorted(parents) for key, parents in deps.items()})
    if route_guards is not None:
        route_guards.update(guards)
    return order


def validate_blueprint(payload, *, dependencies=None, route_guards=None):
    try:
        graph = Blueprint.model_validate(payload)
    except ValidationError as exc:
        code = (
            "GRAPH_UNSUPPORTED"
            if any(
                e["type"] == "union_tag_invalid" or (e["loc"] and e["loc"][-1] == "policy")
                for e in exc.errors()
            )
            else "GRAPH_INVALID"
        )
        raise StudioError(code, "Graph v2 宣告欄位或節點種類不受支援") from exc
    tracked = {} if dependencies is None else dependencies
    order = validate_structure(
        graph,
        max_nodes=200 if graph.graph_contract_version == "2.0" else 401,
        extended=graph.graph_contract_version == "2.2",
        dependencies=tracked,
        route_guards=route_guards,
    )
    from .execution_review import validate_execution_review
    from .pattern_composition import validate_pattern_records

    validate_pattern_records(graph, order, tracked)
    validate_execution_review(graph)
    return graph, order
