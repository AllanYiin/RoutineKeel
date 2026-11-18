import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterator, Protocol
from urllib.parse import urlsplit

from ..contracts import StudioError


@dataclass(frozen=True)
class Chunk:
    text: str = ""
    usage: dict | None = None
    done: bool = False


class Provider(Protocol):
    model: str
    destination: str
    external: bool

    def stream(self, messages: list[dict], max_output_tokens: int) -> Iterator[Chunk]: ...


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ChatCompletionsProvider:
    """OpenAI-compatible SSE transport; no automatic retries or tool invocation."""

    def __init__(
        self, endpoint: str, model: str, *, api_key_env="WORKFLOW_STUDIO_API_KEY", timeout=60
    ):
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not model
            or not 1 <= timeout <= 300
        ):
            raise StudioError(
                "INPUT_INVALID", "模型端點須為無憑證與 query 的 HTTPS URL，並指定模型與逾時。"
            )
        self.destination, self.model = endpoint, model
        self.api_key_env, self.timeout, self.external = api_key_env, timeout, True

    def stream(self, messages, max_output_tokens):
        key = os.environ.get(self.api_key_env)
        if not key:
            raise StudioError("PROVIDER_UNAVAILABLE", "尚未設定模型 API 金鑰環境變數。")
        body = json.dumps(
            {
                "model": self.model,
                "messages": messages,
                "stream": True,
                "stream_options": {"include_usage": True},
                "max_completion_tokens": max_output_tokens,
                "response_format": {"type": "json_object"},
            }
        ).encode()
        request = urllib.request.Request(
            self.destination,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + key,
            },
        )
        try:
            with urllib.request.build_opener(NoRedirect).open(
                request, timeout=self.timeout
            ) as response:
                yield from decode_sse(response, timeout=self.timeout)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise StudioError(
                "LLM_STREAM_INTERRUPTED", "模型連線失敗或中斷；已保留上一個有效版本。"
            ) from exc


def decode_sse(response, *, timeout=60, max_bytes=2 * 1024 * 1024):
    start, consumed, finished = time.monotonic(), 0, False
    while True:
        if time.monotonic() - start > timeout:
            raise StudioError("LLM_STREAM_INTERRUPTED", "模型串流超過時間限制。")
        line = response.readline(65537)
        if not line:
            break
        consumed += len(line)
        if len(line) > 65536 or consumed > max_bytes:
            raise StudioError("LLM_STREAM_INTERRUPTED", "模型回應超過大小限制。")
        if not line.startswith(b"data:"):
            continue
        raw = line[5:].strip()
        if raw == b"[DONE]":
            if not finished:
                raise StudioError("LLM_STREAM_INTERRUPTED", "模型未正常結束輸出。")
            yield Chunk(done=True)
            return
        try:
            payload = json.loads(raw)
            if payload.get("error"):
                raise StudioError("PROVIDER_UNAVAILABLE", "模型服務回報錯誤。")
            for choice in payload.get("choices", []):
                delta = choice.get("delta", {})
                if delta.get("tool_calls") or delta.get("function_call") or delta.get("refusal"):
                    raise StudioError("LLM_STREAM_INTERRUPTED", "模型拒絕或回傳不支援的工具呼叫。")
                content = delta.get("content")
                if content:
                    if not isinstance(content, str) or finished:
                        raise ValueError("Invalid delta")
                    yield Chunk(text=content)
                reason = choice.get("finish_reason")
                if reason is not None:
                    if reason != "stop":
                        raise StudioError("LLM_STREAM_INTERRUPTED", "模型輸出未完整結束。")
                    finished = True
            if payload.get("usage") is not None:
                yield Chunk(usage=payload["usage"])
        except (ValueError, TypeError, AttributeError) as exc:
            raise StudioError("LLM_STREAM_INTERRUPTED", "模型串流格式不合法。") from exc
    raise StudioError("LLM_STREAM_INTERRUPTED", "模型串流缺少完成標記。")
