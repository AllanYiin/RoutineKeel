"""Shared checks for data that cannot be carried across environments."""

import re

_PRIVATE_FIELD = re.compile(
    r"(?:^|[_-])(password|passwd|token|cookie|credential|authorization|api[_-]?key|secret|"
    r"private[_-]?key|account[_-]?id)(?:$|[_-])",
    re.IGNORECASE,
)
_PRIVATE_TEXT = re.compile(
    r"(?:\b[a-z]:[\\/]|\\\\[^\\\s]|(?:^|[\s=:])(?:~[/\\]|/(?!/)[\w.])|"
    r"\b(?:file|secret)://|https?://[^/\s@]+:[^/\s@]+@|[?&](?:token|key|signature)=|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY|\b(?:bearer|password|token|api[_-]?key)\s*[:= ]\s*\S+|"
    r"\b(?:sk-(?:proj-)?[a-z0-9_-]{12,}|gh[pousr]_[a-z0-9]{12,}|AIza[a-z0-9_-]{20,}))",
    re.IGNORECASE,
)


def contains_private_data(value):
    if isinstance(value, dict):
        return any(
            _PRIVATE_FIELD.search(key) or contains_private_data(key) or contains_private_data(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_private_data(item) for item in value)
    return isinstance(value, str) and bool(_PRIVATE_TEXT.search(value))
