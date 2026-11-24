"""Prepare executable acceptance cases while preserving the confirmed case intent."""

import os
import shutil
import tempfile
from mimetypes import guess_type
from pathlib import Path

from routinekeel_contracts.base_steps import DESIGN_KEY, DOCUMENT_INDEXES
from routinekeel_contracts.graph_trials import trial_case_gaps

from ..graph.session import checked_value
from ..outputs import reject_links
from ..runtime import canonical, digest, sha256
from .source import require
from .trial import fixture_destination, fixture_path


def _recipe_case(graph, row):
    if not isinstance(row, dict) or not isinstance(row.get("inputs"), dict):
        return None
    inputs = row["inputs"]
    assertions = row.get("assertions")
    if not isinstance(assertions, dict) or set(assertions) != set(graph["outputs"]):
        return None
    expected = {}
    for name, assertion in assertions.items():
        if not isinstance(assertion, dict):
            return None
        if set(assertion) == {"value"}:
            expected[name] = assertion["value"]
            continue
        if set(assertion) != {"input", "operation"} or not isinstance(
            assertion["input"], str
        ):
            return None
        source = assertion["input"]
        if source not in inputs:
            return None
        if assertion["operation"] == "identity":
            expected[name] = inputs[source]
        elif assertion["operation"] == "uppercase" and isinstance(inputs[source], str):
            expected[name] = inputs[source].upper()
        else:
            return None
    return {
        **{key: row[key] for key in ("config", "replies") if key in row},
        "inputs": inputs,
        "expected": expected,
    }


def case_rows(graph, state):
    quality = graph["quality_contract"]
    declared = quality.get("golden_cases")
    rows = declared if isinstance(declared, list) and declared else [None]
    recipes = quality.get("case_recipes", {})
    recipes = recipes if isinstance(recipes, dict) else {}
    supplied = state.get("case_supplies", {})
    result = []
    for number, original in enumerate(rows, 1):
        location = f"case/{number}"
        confirmed_executable = not trial_case_gaps([original], graph["outputs"])
        if confirmed_executable:
            case, evidence = original, "blueprint"
        elif location in supplied:
            case, evidence = supplied[location]["case"], "user_confirmation"
        else:
            case = _recipe_case(graph, recipes.get(str(number)))
            evidence = "blueprint_assertions" if case is not None else "pending"
        result.append(
            {
                "location": location,
                "intent": original,
                "case": case,
                "evidence": evidence,
            }
        )
    return result


def _case_ports(graph):
    return {
        name: port for name, port in graph["inputs"].items()
        if name != DOCUMENT_INDEXES or DESIGN_KEY not in graph["quality_contract"]
    }


def check_case(root, graph, case):
    require(
        not trial_case_gaps([case], graph["outputs"]),
        "案例須包含輸入及所有具名預期結果。",
    )
    require(len(canonical(case).encode()) <= 2 * 1024**2, "案例內容過大。")
    require(isinstance(case.get("config", {}), dict), "案例設定須為物件。")
    require(isinstance(case.get("replies", []), list), "案例回覆須為清單。")
    ports = _case_ports(graph)
    require(set(case["inputs"]) <= set(ports), "案例含未知輸入。")
    require(
        all(
            name in case["inputs"]
            for name, port in ports.items()
            if port.get("required", True)
        ),
        "案例尚缺必填輸入。",
    )
    for name, value in case["inputs"].items():
        if isinstance(value, dict) and set(value) == {"$file"}:
            require(isinstance(value["$file"], str), "案例素材路徑須為文字。")
            fixture_path(root, value["$file"])
        else:
            checked_value(value, ports[name])


def case_draft(graph):
    ports = _case_ports(graph)
    template = {
        "inputs": {name: None for name in ports},
        "expected": {name: None for name in graph["outputs"]},
        "input_ports": ports,
    }
    transform = {
        "text.copy": lambda value: value,
        "text.upper": str.upper,
        "json.identity": lambda value: value,
    }
    example = {
        "string": "RoutineKeel 測試",
        "integer": 1,
        "number": 1,
        "boolean": True,
        "array": [],
        "object": {},
        "json": {"sample": 1},
        "null": None,
    }
    if any(port["type"] not in example for port in ports.values()):
        return template
    from routinekeel_contracts.graph_validator import validate_blueprint

    _, order = validate_blueprint(graph)
    nodes = {node["id"]: node for node in graph["nodes"]}
    inputs = {name: example[port["type"]] for name, port in ports.items()}
    values = {"input://" + name: value for name, value in inputs.items()}
    for key in order:
        node = nodes[key]
        operation = transform.get(node.get("capability"))
        if (
            node["kind"] != "task"
            or operation is None
            or node.get("parameters")
            or node.get("side_effect") != "none"
            or node.get("permissions")
            or len(node["inputs"]) != 1
            or len(node["outputs"]) != 1
        ):
            return template
        try:
            source = values[next(iter(node["inputs"].values()))["source"]]
            values[f"artifact://{key}/{next(iter(node['outputs']))}"] = operation(
                source
            )
        except (KeyError, TypeError):
            return template
    expected = {name: values[reference] for name, reference in graph["outputs"].items()}
    return {
        **template,
        "inputs": inputs,
        "expected": expected,
        "needs_confirmation": True,
    }


def trial_cases(root, graph, coverage):
    declared = graph["quality_contract"].get("golden_cases")
    required = declared if isinstance(declared, list) and declared else [None]
    require(
        isinstance(coverage, list) and 1 <= len(coverage) == len(required) <= 20,
        "補強後的試做仍須涵蓋全部必要案例。",
    )
    generated = case_rows(graph, {})
    cases = []
    for number, (row, intent, suggested) in enumerate(
        zip(coverage, required, generated, strict=True), 1
    ):
        require(
            row.get("location") == f"case/{number}" and row.get("intent") == intent,
            "補強案例必須對應原已確認的驗收意圖。",
        )
        if not trial_case_gaps([intent], graph["outputs"]):
            require(row["case"] == intent, "必須保留原已確認的可執行案例。")
        elif row.get("evidence") == "blueprint_assertions":
            require(row["case"] == suggested["case"], "案例不符合藍圖的獨立斷言。")
        else:
            require(
                row.get("evidence") == "user_confirmation",
                "文字案例需要已確認的補件或明確斷言。",
            )
        check_case(root, graph, row["case"])
        cases.append(row["case"])
    return cases


def fixture_references(value):
    if isinstance(value, dict):
        if set(value) == {"$file"} and isinstance(value["$file"], str):
            yield value["$file"]
        else:
            for item in value.values():
                yield from fixture_references(item)
    elif isinstance(value, list):
        for item in value:
            yield from fixture_references(item)


def attach_fixture(service, state, source, requested_path=None):
    require(
        isinstance(source, str) and bool(source.strip()), "請指定工作區內的試做素材。"
    )
    path = service._path(source, allow_external=False)
    reject_links(path)
    require(
        path.is_file() and path.stat().st_size <= 512 * 1024**2,
        "試做素材不存在或超過限制。",
    )
    content_hash = sha256(path)
    filename = content_hash + Path(path.name).suffix.lower()
    reference = (
        Path(requested_path)
        if requested_path
        else Path(
            "fixtures",
            "blueprint-completion",
            state["work_id"],
            filename,
        )
    )
    target = fixture_destination(service.workspace.root, reference.as_posix())
    runtime = service.workspace.runtime
    with runtime.lock("blueprint-fixture:" + reference.as_posix(), timeout=10):
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix=".fixture-", dir=target.parent)
            temporary = Path(temp)
            try:
                with os.fdopen(fd, "wb") as destination, path.open("rb") as origin:
                    shutil.copyfileobj(origin, destination)
                    destination.flush()
                    os.fsync(destination.fileno())
                require(
                    temporary.stat().st_size <= 512 * 1024**2
                    and sha256(temporary) == content_hash,
                    "素材在複製期間變更，請重新選取。",
                )
                require(not target.exists(), "補件位置已存在，請核對目前素材。")
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        require(
            sha256(target) == content_hash, "補件位置已存在不同內容，請核對原素材。"
        )
    return {
        "id": digest([state["work_id"], content_hash, filename])[:24],
        "name": path.name,
        "size": target.stat().st_size,
        "media_type": guess_type(path.name)[0] or "application/octet-stream",
        "sha256": content_hash,
        "reference": {"$file": reference.as_posix()},
    }
