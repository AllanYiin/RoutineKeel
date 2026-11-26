from threading import Barrier, Event

import pytest

from flow_base.graph import qualification, tool_calls
from flow_base.runtime import FlowError, read_json


def proposal(ident, source):
    return {"id": ident, "function": {"name": "echo", "arguments": {"source": source}}}


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setattr(tool_calls, "receipt_path", lambda *_args: tmp_path / "receipt.json")
    port = {"type": "string"}
    node = {"inputs": {"source": {"port": port}}, "outputs": {"result": port}}
    registry = {"echo": {"node": node, "binding": {"provider": "builtin", "tool": "text.copy"}}}
    task = {"idempotency_key": "a" * 64, "scope_hash": "b" * 64, "inputs": {},
            "scope": {"tool_registry": registry, "resources": {"model_calls": 3}}}
    return tool_calls.ToolRoundJournal(tmp_path, task, {"max_rounds": 3, "max_tool_calls": 2})


def test_independent_calls_reserve_first_and_finish_in_original_order(journal, monkeypatch):
    overlap = Barrier(2, timeout=5)
    fast_result = Event()
    invoked = []

    def execute(_binding, _node, inputs, _parameters, **_env):
        saved = read_json(journal.path)
        assert [row["outcome"] for row in saved["tool_requests"].values()] == ["unknown", "unknown"]
        assert saved["model_requests"][0]["outcome"] == "observed"
        source = inputs["source"]
        invoked.append(source)
        overlap.wait()
        if source == "slow":
            assert fast_result.wait(timeout=5)
        else:
            fast_result.set()
        return {"result": source}

    monkeypatch.setattr(qualification, "execute_local", execute)
    journal.begin_model_call()
    assert read_json(journal.path)["model_requests"] == [{"outcome": "unknown"}]
    journal.end_model_call({"prompt_tokens": 5, "completion_tokens": 2})
    replies = journal.dispatch_tools([proposal("first", "slow"), proposal("second", "fast")])
    assert [reply["tool_call_id"] for reply in replies] == ["first", "second"]
    assert set(invoked) == {"slow", "fast"}
    assert journal.dispatch_tools([proposal("second", "fast")]) == replies[1:]
    assert len(invoked) == journal.usage["tool_calls"] == 2
    with pytest.raises(FlowError, match="不一致"):
        journal.dispatch_tools([proposal("first", "changed")])
    with pytest.raises(FlowError, match="超出"):
        journal.dispatch_tools([proposal("third", "new")])


def test_tool_round_limits_and_file_scope_are_checked_before_execution(journal, monkeypatch):
    monkeypatch.setattr(qualification, "execute_local", lambda *_args, **_kwargs: pytest.fail("tool invoked"))
    journal.begin_model_call()
    with pytest.raises(FlowError, match="素材"):
        journal.dispatch_tools([proposal("file", "asset:" + "a" * 64 + ".txt")])
    for _ in range(2):
        journal.begin_model_call()
    with pytest.raises(FlowError, match="回填模型"):
        journal.dispatch_tools([proposal("late", "text")])
    with pytest.raises(FlowError, match="模型輪次"):
        journal.begin_model_call()
    assert read_json(journal.path)["tool_requests"] == {}
