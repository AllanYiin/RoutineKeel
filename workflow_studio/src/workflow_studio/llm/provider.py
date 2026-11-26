import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Iterator, Protocol
from urllib.parse import urlsplit

from ..contracts import StudioError


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class Chunk:
    text: str = ""
    usage: dict | None = None
    done: bool = False
    tool_calls: tuple[ToolCall, ...] = ()


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
        yield from self._stream(messages, max_output_tokens, tools=None)

    def stream_tools(self, messages, max_output_tokens, tools):
        if not isinstance(tools, list) or not tools:
            raise StudioError("INPUT_INVALID", "工具模式需要核對過的具名工具清單。")
        yield from self._stream(messages, max_output_tokens, tools=tools)

    def _stream(self, messages, max_output_tokens, *, tools):
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
                **({"response_format": {"type": "json_object"}} if tools is None else {"tools": tools}),
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
                yield from decode_sse(response, timeout=self.timeout, allow_tools=tools is not None)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise StudioError(
                "LLM_STREAM_INTERRUPTED", "模型連線失敗或中斷；已保留上一個有效版本。"
            ) from exc


@dataclass
class _ToolDraft:
    id: str | None = None
    name: str | None = None
    arguments: list[str] = field(default_factory=list)

    def append(self, delta):
        if delta.get("type") not in {None, "function"}:
            raise ValueError("Invalid tool type")
        ident = delta.get("id")
        function = delta.get("function")
        if function is not None and not isinstance(function, dict):
            raise ValueError("Invalid tool function delta")
        function = function or {}
        name = function.get("name")
        if ident is not None:
            if (not isinstance(ident, str) or not 1 <= len(ident) <= 200
                    or not ident.isprintable() or any(character.isspace() for character in ident)
                    or self.id not in {None, ident}):
                raise ValueError("Invalid or conflicting tool ID")
            self.id = ident
        if name is not None:
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)
                    or self.name not in {None, name}):
                raise ValueError("Invalid or conflicting tool name")
            self.name = name
        if (arguments := function.get("arguments")) is not None:
            if not isinstance(arguments, str):
                raise ValueError("Invalid tool argument fragment")
            self.arguments.append(arguments)

    def complete(self):
        if not self.id or not self.name:
            raise ValueError("Missing tool identity")

        def unique_members(pairs):
            result = dict(pairs)
            if len(result) != len(pairs):
                raise ValueError("Duplicate tool argument member")
            return result

        arguments = json.loads("".join(self.arguments), object_pairs_hook=unique_members)
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")
        json.dumps(arguments, allow_nan=False)
        return ToolCall(self.id, self.name, arguments)


def _completed_calls(drafts):
    if set(drafts) != set(range(len(drafts))):
        raise ValueError("Noncontiguous tool call indexes")
    calls = {}
    for index in range(len(drafts)):
        call = drafts[index].complete()
        prior = calls.setdefault(call.id, call)
        if prior != call:
            raise ValueError("Conflicting tool call ID")
    return tuple(calls.values())


def decode_sse(response, *, timeout=60, max_bytes=2 * 1024 * 1024, allow_tools=False):
    start, consumed, finished = time.monotonic(), 0, False
    drafts = {}
    proposals = ()
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
            yield Chunk(done=True, tool_calls=proposals)
            return
        try:
            payload = json.loads(raw)
            if payload.get("error"):
                raise StudioError("PROVIDER_UNAVAILABLE", "模型服務回報錯誤。")
            for choice in payload.get("choices", []):
                delta = choice.get("delta", {})
                fragments = delta.get("tool_calls")
                if delta.get("function_call") or delta.get("refusal") or (fragments and not allow_tools):
                    raise StudioError("LLM_STREAM_INTERRUPTED", "模型拒絕或回傳不支援的工具呼叫。")
                if allow_tools:
                    index = choice.get("index", 0)
                    if type(index) is not int or index != 0:
                        raise ValueError("Multiple choices cannot share a tool round")
                    if fragments is not None and not isinstance(fragments, list):
                        raise ValueError("Invalid tool call delta")
                if fragments:
                    if finished:
                        raise ValueError("Invalid tool call delta")
                    for fragment in fragments:
                        index = fragment.get("index")
                        if type(index) is not int or not 0 <= index < 4:
                            raise ValueError("Tool call index exceeds the round limit")
                        drafts.setdefault(index, _ToolDraft()).append(fragment)
                content = delta.get("content")
                if content:
                    if not isinstance(content, str) or finished:
                        raise ValueError("Invalid delta")
                    yield Chunk(text=content)
                reason = choice.get("finish_reason")
                if reason is not None:
                    if finished and allow_tools:
                        raise ValueError("Duplicate finish reason")
                    if reason == "tool_calls" and allow_tools and drafts:
                        proposals = _completed_calls(drafts)
                    elif reason != "stop" or drafts:
                        raise StudioError("LLM_STREAM_INTERRUPTED", "模型輸出未完整結束。")
                    finished = True
            if payload.get("usage") is not None:
                yield Chunk(usage=payload["usage"])
        except (ValueError, TypeError, AttributeError, RecursionError) as exc:
            raise StudioError("LLM_STREAM_INTERRUPTED", "模型串流格式不合法。") from exc
    raise StudioError("LLM_STREAM_INTERRUPTED", "模型串流缺少完成標記。")
