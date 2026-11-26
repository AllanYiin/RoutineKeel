import io
import json

import pytest
from workflow_studio.contracts import StudioError
from workflow_studio.llm.provider import ChatCompletionsProvider, NoRedirect, decode_sse


def sse(*payloads):
    return io.BytesIO(
        b"".join(
            b"data: " + (p if isinstance(p, bytes) else json.dumps(p).encode()) + b"\n\n"
            for p in payloads
        )
    )


def test_sse_unicode_finish_and_final_usage():
    stream = sse(
        {"choices": [{"delta": {"content": "繁體中文"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 3}},
        b"[DONE]",
    )
    chunks = list(decode_sse(stream))
    assert chunks[0].text == "繁體中文"
    assert chunks[1].usage["prompt_tokens"] == 12
    assert chunks[-1].done is True


@pytest.mark.parametrize(
    "stream",
    [
        b"data: {invalid}\n",
        b"data: [DONE]\n",
        b'data: {"choices":[{"delta":{"content":"partial"}}]}\n',
        b'data: {"choices":[{"delta":{},"finish_reason":"length"}]}\n',
        b'data: {"choices":[{"delta":{"tool_calls":[{}]}}]}\n',
    ],
)
def test_invalid_or_interrupted_stream_rejected(stream):
    with pytest.raises(StudioError):
        list(decode_sse(io.BytesIO(stream)))


def test_transport_sets_stream_limits_without_sending_keys_in_body(monkeypatch):
    captured = {}

    class Opener:
        def open(self, request, timeout):
            captured.update(body=json.loads(request.data), headers=request.headers, timeout=timeout)
            return sse(
                {"choices": [{"delta": {"content": "{}"}, "finish_reason": "stop"}]}, b"[DONE]"
            )

    monkeypatch.setenv("STUDIO_TEST_KEY", "test-key")
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    provider = ChatCompletionsProvider(
        "https://example.invalid/v1/chat/completions", "test-model", api_key_env="STUDIO_TEST_KEY"
    )
    assert list(provider.stream([{"role": "user", "content": "fixture"}], 10))[-1].done
    assert captured["body"]["stream_options"]["include_usage"] is True
    assert captured["body"]["max_completion_tokens"] == 10
    assert "tools" not in captured["body"]
    assert "test-key" not in json.dumps(captured["body"])
    assert (
        NoRedirect().redirect_request(None, None, 302, None, None, "https://other.invalid") is None
    )


def test_missing_key_and_untrusted_endpoint_rejected(monkeypatch):
    monkeypatch.delenv("MISSING_TEST_KEY", raising=False)
    provider = ChatCompletionsProvider(
        "https://example.invalid/v1/chat/completions", "test", api_key_env="MISSING_TEST_KEY"
    )
    with pytest.raises(StudioError) as caught:
        list(provider.stream([], 10))
    assert caught.value.code == "PROVIDER_UNAVAILABLE"
    with pytest.raises(StudioError):
        ChatCompletionsProvider("http://example.invalid/", "test")


def tool_fragment(index, *, ident=None, name=None, arguments=None):
    return {"index": index, "id": ident, "type": "function" if ident else None,
            "function": {"name": name, "arguments": arguments}}


def tool_delta(*fragments):
    return {"choices": [{"index": 0, "delta": {"tool_calls": list(fragments)}}]}


TOOL_FINISH = {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}


def test_explicit_tool_mode_assembles_interleaved_arguments_in_index_order():
    chunks = list(decode_sse(sse(
        tool_delta(tool_fragment(1, ident="call_second", name="echo", arguments='{"text":"')),
        tool_delta(tool_fragment(0, ident="call_first", name="echo", arguments='{"text":')),
        tool_delta(tool_fragment(1, arguments='二"}'), tool_fragment(0, arguments='"一"}')),
        TOOL_FINISH, {"choices": [], "usage": {"prompt_tokens": 16}}, b"[DONE]",
    ), allow_tools=True))
    assert all(not chunk.tool_calls for chunk in chunks[:-1])
    assert chunks[-2].usage == {"prompt_tokens": 16}
    assert [(call.id, call.name, call.arguments) for call in chunks[-1].tool_calls] == [
        ("call_first", "echo", {"text": "一"}), ("call_second", "echo", {"text": "二"}),
    ]
    assert chunks[-1].done


@pytest.mark.parametrize("payloads", [
    [tool_delta(tool_fragment(0, ident="same", name="echo", arguments='{"x":')),
     TOOL_FINISH, b"[DONE]"],
    [tool_delta(tool_fragment(0, ident="same", name="echo", arguments='{"x":1,"x":2}')),
     TOOL_FINISH, b"[DONE]"],
    [tool_delta(tool_fragment(0, ident="same", name="echo", arguments="{}")), TOOL_FINISH],
    [tool_delta(tool_fragment(4, ident="extra", name="echo", arguments="{}")), TOOL_FINISH, b"[DONE]"],
    [tool_delta(tool_fragment(0, ident="same", name="echo", arguments="{}"),
                tool_fragment(1, ident="same", name="echo", arguments='{"x":1}')),
     TOOL_FINISH, b"[DONE]"],
])
def test_tool_mode_does_not_publish_incomplete_or_conflicting_calls(payloads):
    with pytest.raises(StudioError, match="模型串流"):
        list(decode_sse(sse(*payloads), allow_tools=True))


def test_transport_offers_tools_only_in_explicit_tool_mode(monkeypatch):
    captured = {}

    class Opener:
        def open(self, request, timeout):
            captured.update(json.loads(request.data))
            return sse(tool_delta(tool_fragment(0, ident="a", name="echo", arguments="{}"),
                                  tool_fragment(1, ident="a", name="echo", arguments="{}")),
                       TOOL_FINISH, b"[DONE]")

    monkeypatch.setenv("STUDIO_TEST_KEY", "test-key")
    monkeypatch.setattr("urllib.request.build_opener", lambda *args: Opener())
    provider = ChatCompletionsProvider("https://example.invalid", "test", api_key_env="STUDIO_TEST_KEY")
    tools = [{"type": "function", "function": {"name": "echo", "parameters": {"type": "object"}}}]
    end = list(provider.stream_tools([], 20, tools))[-1]
    assert len(end.tool_calls) == 1 and end.tool_calls[0].id == "a"
    assert captured["tools"] == tools and "response_format" not in captured
