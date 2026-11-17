import json

import pytest
from workflow_studio.compiler.runtime_program import (
    Limits,
    ProgramError,
    run_program,
    validate_program,
)


@pytest.mark.parametrize(
    "body",
    [
        "import os\n    return text",
        "return open('/private').read()",
        "return text.__class__.__mro__",
        "return getattr(text, 'upper')()",
        "return eval(text)",
        "return __import__('os').getcwd()",
        "while True:\n        pass",
        "return transform(text, params)",
        "def inner():\n        return text\n    return inner()",
        "return (lambda: text)()",
        "return [x for x in globals()]",
        "return text.format(**params)",
        "break",
    ],
)
def test_unsafe_capability_rejected_before_execution(body):
    with pytest.raises(ProgramError):
        validate_program("def transform(text, params):\n    " + body)


def test_no_file_or_network_access_is_exposed(monkeypatch):
    import builtins
    import socket

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Generated transforms cannot use ambient capabilities")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    source = 'def transform(text, params):\n    rows = json_loads(text)\n    return json_dumps([row["name"].upper() for row in rows])'
    assert json.loads(run_program(source, '[{"name":"alpha"}]')) == ["ALPHA"]


@pytest.mark.parametrize(
    "body",
    [
        'return "x" * 1000000000',
        'return text.replace("", "x" * 80, -1)',
        "return json_dumps(json_loads(text), indent=1000000000)",
        "return str(list(range(1000000000)))",
        "items = []\n    items.append(items)\n    return str(items)",
        "a = []\n    b = [a]\n    a.append(b)\n    return str(a)",
        'a = {}\n    a["self"] = a\n    return str(a)',
        'return "%1000000000s" % text',
    ],
)
def test_expansion_and_cycles_fail_closed(body):
    with pytest.raises(ProgramError):
        run_program(
            "def transform(text, params):\n    " + body,
            '{"value":"many characters"}',
            limits=Limits(max_bytes=256, max_items=20, max_steps=1000),
        )


def test_nested_loops_are_bounded():
    code = "def transform(text, params):\n    total = 0\n    for a in range(500):\n        for b in range(500):\n            total += 1\n    return str(total)"
    with pytest.raises(ProgramError, match="operation/time"):
        run_program(code, "", limits=Limits(max_steps=1000))


def test_sparse_csv_cannot_expand_missing_cells_indefinitely():
    text = ",".join(f"column{i}" for i in range(100)) + "\n" + "1\n" * 200
    with pytest.raises(ProgramError):
        run_program(
            "def transform(text, params):\n    return json_dumps(csv_read(text))",
            text,
            limits=Limits(max_bytes=5000),
        )


def test_arguments_are_local_and_json_ambiguity_rejected():
    args = {"name": "original"}
    assert (
        run_program(
            'def transform(text, params):\n    params["name"] = "changed"\n    return params["name"]',
            "",
            args,
        )
        == "changed"
    )
    assert args == {"name": "original"}
    with pytest.raises(ProgramError):
        run_program(
            "def transform(text, params):\n    return json_dumps(json_loads(text))", '{"a":1,"a":2}'
        )


def test_repeated_collection_views_count_toward_allocation_limit():
    source = "def transform(text, params):\n    data = json_loads(text)\n    for i in range(50):\n        keys = data.keys()\n    return str(len(keys))"
    with pytest.raises(ProgramError, match="allocation"):
        run_program(
            source,
            json.dumps({str(i): i for i in range(20)}),
            limits=Limits(max_bytes=10000, max_allocation=5000),
        )


def test_tuple_semantics_and_hidden_unsafe_branch():
    assert (
        run_program("def transform(text, params):\n    return str((1, 2) == [1, 2])", "") == "False"
    )
    with pytest.raises(ProgramError):
        validate_program(
            "def transform(text, params):\n    if False:\n        import os\n    return text"
        )
