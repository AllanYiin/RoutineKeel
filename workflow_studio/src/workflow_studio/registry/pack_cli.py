"""Local text interface to the Studio domain catalog."""

import sys
from secrets import token_hex

from routinekeel_contracts.console import interactive_console

from ..contracts import StudioError
from .packs import CatalogDecision

_DECISIONS = {"approve", "disable", "archive", "revoke", "deprecate"}


def _local_decision(operation, item, digest, payload):
    if not interactive_console():
        raise StudioError("PATTERN_APPROVAL_REQUIRED", "請在本機互動終端確認相同內容與版本。")
    challenge = token_hex(4)
    qualification = payload.get("qualification_hash")
    print(
        f"{operation} {item['ref']}\n版本指紋 {digest}\n紀錄修訂 {item['revision']}",
        file=sys.stderr,
    )
    if qualification:
        print(f"試做收據 {qualification}", file=sys.stderr)
    print(f"輸入 {challenge} 確認。其他輸入會取消。", file=sys.stderr)
    if input().strip() != challenge:
        raise StudioError("PATTERN_APPROVAL_REQUIRED", "這次決策未獲確認。")
    print("請輸入決策者名稱", file=sys.stderr)
    actor = input().strip()
    print("請輸入具體理由", file=sys.stderr)
    reason = input().strip()
    return CatalogDecision(
        operation,
        item["ref"],
        digest,
        item["revision"],
        actor,
        reason,
        "local_terminal",
        qualification,
    )


def dispatch(studio, operation, payload, *, runtime_python=None):
    registry = studio.tools.packs
    if operation == "register":
        return registry.register(payload["path"])
    if operation == "list":
        return registry.list(include_archived=payload.get("include_archived", False))
    if operation == "patterns":
        return registry.patterns(domain=payload.get("domain"))
    if operation == "get":
        return registry.get(payload["ref"])
    if operation == "history":
        return registry.history(payload["ref"], kind=payload.get("kind", "pack"))
    if operation == "dependents":
        return registry.dependents(payload["ref"])
    if operation == "qualify":
        from ..patterns.qualification import qualify

        return qualify(studio, payload["pack_ref"], payload["ref"], runtime_python=runtime_python)
    if operation == "trial":
        return registry.trial(payload["ref"], payload["qualification_hash"])
    if operation in _DECISIONS:
        ref = payload["ref"]
        kind = payload.get("kind", "pack")
        if kind == "pattern":
            item = registry.pattern_status(ref)
            decision = _local_decision(operation, item, item["content_hash"], payload)
            return registry.decide_pattern(decision)
        if kind == "pack":
            item = registry.get(ref)
            decision = _local_decision(operation, item, item["manifest_hash"], payload)
            return registry.decide(decision)
    raise StudioError("INPUT_INVALID", "未知的領域資產操作。")
