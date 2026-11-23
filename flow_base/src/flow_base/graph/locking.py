"""Atomic versioned JSON locks, indexed by the existing Runtime SQLite store."""

import json
import re
import sqlite3
from contextlib import closing

from pydantic import ValidationError
from routinekeel_contracts.bindings import EnvironmentLock
from routinekeel_contracts.domain_assets import content_hash
from routinekeel_contracts.privacy import contains_private_data

from ..core.lifecycle import utc_now
from ..outputs import reject_links
from ..runtime import FlowError, atomic_json, canonical, read_json
from ..workspace import Workspace

_HASH = re.compile(r"^[a-f0-9]{64}$")


def _runtime(workspace):
    return Workspace(workspace).runtime


def _lock_path(runtime, lock_hash):
    if not _HASH.fullmatch(lock_hash):
        raise FlowError("BINDING_STALE", "環境鎖定指紋不合法。")
    path = runtime.home / "portable_locks" / (lock_hash + ".json")
    reject_links(path)
    return path


def write_receipts(workspace, graph_hash, reports):
    runtime = _runtime(workspace)
    runtime.initialize()
    with closing(runtime.connect()) as db, db:
        for node, report in reports.items():
            if contains_private_data(report):
                raise FlowError("SECRET_IN_PORTABLE_PACKAGE", "資格證據含私人資料。")
            db.execute(
                "INSERT OR IGNORE INTO adapter_qualifications VALUES (?,?,?,?,?,?)",
                (
                    content_hash(report),
                    graph_hash,
                    node,
                    report["verdict"],
                    canonical(report),
                    utc_now(),
                ),
            )


def persist_lock(workspace, lock, reports, *, current=True):
    runtime = _runtime(workspace)
    runtime.initialize()
    payload = {**lock.model_dump(mode="json"), "lock_hash": lock.lock_hash}
    if contains_private_data(payload):
        raise FlowError("SECRET_IN_PORTABLE_PACKAGE", "環境鎖定含私人資料。")
    write_receipts(workspace, lock.graph_hash, reports)
    with runtime.lock("portable-bindings:" + lock.graph_hash, timeout=20):
        path = _lock_path(runtime, lock.lock_hash)
        if path.is_file():
            if read_json(path) != payload:
                raise FlowError("BINDING_STALE", "環境鎖定的內容已改變。")
        else:
            atomic_json(path, payload)
        with closing(runtime.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR IGNORE INTO portable_binding_locks VALUES (?,?,?,?)",
                (lock.lock_hash, lock.graph_hash, canonical(payload), utc_now()),
            )
            if current:
                db.execute(
                    """INSERT INTO portable_binding_heads VALUES (?,?)
                    ON CONFLICT(graph_hash) DO UPDATE SET lock_hash=excluded.lock_hash""",
                    (lock.graph_hash, lock.lock_hash),
                )
    return payload


def read_lock(workspace, graph_hash, *, lock_hash=None):
    runtime = _runtime(workspace)
    state = runtime.home / "state.sqlite3"
    reject_links(state)
    if not state.is_file():
        return None
    try:
        with closing(sqlite3.connect(state.as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < 6:
                return None
            if lock_hash is None:
                row = db.execute(
                    "SELECT lock_hash FROM portable_binding_heads WHERE graph_hash=?", (graph_hash,)
                ).fetchone()
                if row is None:
                    return None
                lock_hash = row[0]
            row = db.execute(
                "SELECT body FROM portable_binding_locks WHERE lock_hash=?", (lock_hash,)
            ).fetchone()
            if row is None:
                raise FlowError("BINDING_STALE", "找不到已記錄的環境鎖定。")
            payload = json.loads(row["body"])
            recorded = payload.pop("lock_hash")
            lock = EnvironmentLock.model_validate(payload)
            if (
                lock.lock_hash != recorded
                or recorded != lock_hash
                or graph_hash is not None
                and lock.graph_hash != graph_hash
                or read_json(_lock_path(runtime, recorded)) != {**payload, "lock_hash": recorded}
                or contains_private_data(payload)
            ):
                raise FlowError("BINDING_STALE", "環境鎖定指紋或來源不一致。")
            evidence = {}
            for node, binding in lock.bindings.items():
                receipt = db.execute(
                    """SELECT body FROM adapter_qualifications WHERE receipt=? AND graph_hash=?
                    AND node=? AND verdict='PASS'""",
                    (binding.qualification_receipt, lock.graph_hash, node),
                ).fetchone()
                if not receipt:
                    raise FlowError("BINDING_STALE", "環境鎖定缺少必要資格收據。")
                report = json.loads(receipt["body"])
                if (
                    content_hash(report) != binding.qualification_receipt
                    or report.get("verdict") != "PASS"
                    or report.get("conformance", {}).get("status") != "PASS"
                    or report.get("behavior", {}).get("status") != "PASS"
                    or report.get("graph_hash") != lock.graph_hash
                    or report.get("environment_fingerprint") != binding.environment_fingerprint
                ):
                    raise FlowError("BINDING_STALE", "兩階段資格證據與環境鎖定不同。")
                evidence[node] = report
    except (OSError, sqlite3.Error, ValueError, KeyError, ValidationError) as exc:
        raise FlowError("BINDING_STALE", "無法驗證環境鎖定及收據。") from exc
    return lock, evidence


def copy_lock_for_trial(source, target, graph_hash, lock_hash):
    result = read_lock(source, graph_hash, lock_hash=lock_hash)
    if result is None:
        raise FlowError("BINDING_STALE", "試做缺少已驗證的環境鎖定。")
    lock, receipts = result
    return persist_lock(target, lock, receipts)
