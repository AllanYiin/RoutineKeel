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
