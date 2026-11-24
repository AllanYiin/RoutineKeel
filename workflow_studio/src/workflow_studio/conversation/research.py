"""Fetch public primary-source passages and keep local, content-bound evidence."""

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from html.parser import HTMLParser
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..contracts import StudioError
from ..governance.release import atomic_bytes

_PRIMARY_HOSTS = {
    "arxiv.org",
    "csrc.nist.gov",
    "developers.google.com",
    "developers.openai.com",
    "docs.anthropic.com",
    "docs.aws.amazon.com",
    "docs.nvidia.com",
    "docs.ollama.com",
    "docs.python.org",
    "docs.pytorch.org",
    "ffmpeg.org",
    "huggingface.co",
    "learn.microsoft.com",
    "platform.openai.com",
    "www.ffmpeg.org",
    "www.itu.int",
}
_LIMIT = 2 * 1024**2


def normalize(value):
    return " ".join(value.split())


def check_url(url):
    try:
        parts = urlsplit(url)
        if (
            parts.scheme == "https"
            and parts.hostname in _PRIMARY_HOSTS
            and parts.username is None
            and parts.password is None
            and parts.port in {443, None}
            and not re.search(
                r"(?:^|[&?])(?:key|token|secret|signature|authorization)=", parts.query, re.I
            )
            and not any(ord(char) < 32 for char in url)
        ):
            return url
    except (TypeError, ValueError):
        pass
    raise StudioError("INPUT_INVALID", "請提供受支援的公開第一手 HTTPS 來源。")


class _Redirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = set()

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template", "noscript"}:
            self.hidden.add(tag)
        elif tag in {"p", "div", "li", "td", "th", "br", "pre", "section", "dt", "dd"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        self.hidden.discard(tag)
        if tag in {"p", "div", "li", "td", "th", "pre", "section", "dt", "dd"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _fetch(url):
    request = Request(
        check_url(url),
        headers={
            "User-Agent": "RoutineKeel-Research/1.0",
            "Accept": "text/html, text/plain, text/markdown",
        },
    )
    try:
        with build_opener(_Redirects()).open(request, timeout=12) as response:
            check_url(response.geturl())
            media = response.headers.get_content_type()
            data = response.read(_LIMIT + 1)
            charset = response.headers.get_content_charset() or "utf-8"
    except (URLError, OSError, ValueError) as error:
        raise StudioError("DEPENDENCY_UNAVAILABLE", "無法讀取指定的公開來源。") from error
    if len(data) > _LIMIT or media not in {"text/html", "text/plain", "text/markdown"}:
        raise StudioError("INPUT_INVALID", "來源須為可讀的公開文字頁面，且小於 2 MiB。")
    try:
        text = data.decode(charset)
    except (LookupError, UnicodeError) as error:
        raise StudioError("INPUT_INVALID", "無法解讀來源文字的編碼。") from error
    if media == "text/html":
        parser = _VisibleText()
        parser.feed(text)
        text = "".join(parser.parts)
    return normalize(text)


def _location(repository, url, source_sha256, accessed_on):
    root = repository.root / "research_sources"
    key = hashlib.sha256(f"{url}\n{source_sha256}\n{accessed_on}".encode()).hexdigest()
    path = root / f"{key}.json"
    for item in (root, path):
        if item.is_symlink() or getattr(item, "is_junction", lambda: False)():
            raise StudioError("INPUT_INVALID", "研究紀錄不能使用連結。")
    return path


def observe_source(repository, url, excerpt):
    check_url(url)
    if not isinstance(excerpt, str) or not 12 <= len(normalize(excerpt)) <= 1500:
        raise StudioError("INPUT_INVALID", "請提供一段 12–1500 字、可在來源找到的原文。")
    text = _fetch(url)
    if normalize(excerpt) not in text:
        raise StudioError("INPUT_INVALID", "提供的引文與實際頁面不符。")
    result = {
        "url": url,
        "accessed_on": datetime.now(UTC).date().isoformat(),
        "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }
    path = _location(repository, url, result["source_sha256"], result["accessed_on"])
    atomic_bytes(path, json.dumps({**result, "text": text}).encode())
    return result


def verify_source(repository, source):
    path = _location(repository, check_url(source.url), source.source_sha256, source.accessed_on)
    try:
        if path.stat().st_size > _LIMIT * 6:
            raise ValueError("source too large")
        evidence = json.loads(path.read_bytes())
        text = evidence["text"]
        numbers = re.findall(
            r"(?<![\w.])[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?!\w|\.\d)", source.excerpt
        )
        if (
            evidence["url"] == source.url
            and evidence["accessed_on"] == source.accessed_on
            and evidence["source_sha256"] == source.source_sha256
            and hashlib.sha256(text.encode()).hexdigest() == source.source_sha256
            and normalize(source.excerpt) in text
            and any(Decimal(number) == Decimal(str(source.recommended_value)) for number in numbers)
        ):
            return
    except (OSError, ValueError, TypeError, KeyError):
        pass
    raise StudioError("BLUEPRINT_BLOCKED", "請先用研究入口查核引文，並使用該來源紀錄與其中的數值。")
