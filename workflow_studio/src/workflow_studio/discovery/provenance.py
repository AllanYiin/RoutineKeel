"""Observed equality is evidence about a sample, never proof of historical causation."""

import hashlib

from ..contracts import StudioError
from ..registry.builtin import TRANSFORMS


def replay_candidates(source: bytes, expected: bytes) -> list[dict]:
    matches = []
    definitions = [
        ("file.copy", "builtin.copy", {}),
        ("text.normalize", "builtin.normalize", {"strip": True}),
        ("text.normalize", "builtin.normalize", {"strip": False}),
        ("json.format", "builtin.json", {}),
        ("file.report", "builtin.report", {}),
    ]
    for tool, implementation, parameters in definitions:
        try:
            actual = TRANSFORMS[implementation](source, parameters)
        except (ValueError, UnicodeError, StudioError):
            continue
        if actual == expected:
            matches.append(
                {
                    "tool": tool,
                    "tool_version": "1.0.0",
                    "parameters": parameters,
                    "sample_match": True,
                    "expected_sha256": hashlib.sha256(expected).hexdigest(),
                    "confidence_label": "inferred",
                    "claim": "此工具可重現這組樣本；不能據此證明歷史流程或所有例外。",
                }
            )
    return matches
