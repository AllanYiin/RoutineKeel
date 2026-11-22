"""Scoped external requests. Only the host records a separately observed execution receipt."""

import os
import re
from pathlib import Path

from filelock import FileLock

from ..blueprints.source import require
from ..outputs import reject_links
from ..runtime import atomic_json, digest, read_json


def scope_for(session_id, node_id, attempt, binding, values, configuration, *, parameters=None):
    scope = {
        "session_id": session_id,
        "node_id": node_id,
        "attempt": attempt,
        "tool": binding["tool"],
        "version": binding["version"],
        "binding_fingerprint": binding.get("fingerprint", binding.get("authority_hash")),
        "destination": binding.get("destination") or binding.get("tool"),
        "permissions": binding.get("permissions", []),
        "resources": binding.get("resources"),
        "arguments": values,
        "configuration_hash": digest(configuration),
    }
    if parameters:
        scope["parameters"] = parameters
    return scope, digest(scope)


def receipt_path(workspace, key):
    require(isinstance(key, str) and re.fullmatch(r"[0-9a-f]{64}", key), "對外操作識別碼無效")
    configured = os.environ.get("ROUTINEKEEL_HOST_CATALOG")
    require(configured and Path(configured).is_absolute(), "缺少宿主回執來源")
    root = Path(configured).resolve().parent / "receipts"
    require(
        not root.resolve().is_relative_to(Path(workspace).resolve()), "宿主回執不能來自資料工作區"
    )
    path = root / (key + ".json")
    reject_links(path)
    return path


def record_observation(
    workspace, key, scope_hash, provider_receipt, outcome, outputs, *, usage=None, stream=None
):
    """Host integration API. No CLI or MCP tool exposes this writer to the workflow."""
    path = receipt_path(workspace, key)
    require(
        isinstance(provider_receipt, str)
        and provider_receipt.strip()
        and outcome in {"succeeded", "not_executed", "failed"},
        "缺少獨立工具觀測",
    )
    receipt = {
        "key": key,
        "scope_hash": scope_hash,
        "provider_receipt": provider_receipt,
        "outcome": outcome,
        "outputs": outputs,
        "outputs_hash": digest(outputs),
    }
    if usage is not None:
        require(
            isinstance(usage, dict)
            and {"model_calls", "cost_micro_usd"} <= usage.keys()
            and all(type(n) is int and n >= 0 for n in usage.values()),
            "用量回執無效",
        )
        receipt["usage"] = usage
    if stream is not None:
        require(
            isinstance(stream, dict)
            and stream.get("completed") is True
            and type(stream.get("count")) is int
            and stream["count"] > 0
            and re.fullmatch(r"[0-9a-f]{64}", str(stream.get("hash", ""))),
            "完成回執需要完整串流指紋",
        )
        receipt["stream"] = stream
    path.parent.mkdir(exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=10):
        if path.exists():
            previous = read_json(path)
            require(
                all(receipt.get(k) == v for k, v in previous.items())
                and set(receipt) - set(previous) <= {"usage", "stream"},
                "同一操作有不一致的回執",
            )
            if previous != receipt:
                snapshot = path.with_name(path.stem + "-" + digest(previous) + ".json")
                if not snapshot.exists():
                    atomic_json(snapshot, previous)
                atomic_json(path, receipt)
        else:
            atomic_json(path, receipt)
    return receipt


def read_observation(workspace, key, scope_hash):
    path = receipt_path(workspace, key)
    if not path.is_file():
        return None
    receipt = read_json(path)
    require(
        receipt.get("key") == key
        and receipt.get("scope_hash") == scope_hash
        and receipt.get("provider_receipt")
        and receipt.get("outcome") in {"succeeded", "not_executed", "failed"}
        and digest(receipt.get("outputs")) == receipt.get("outputs_hash"),
        "工具回執與本次操作不符",
    )
    return receipt
