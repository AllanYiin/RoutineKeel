import json
import re
from contextlib import closing

from ..blueprints.source import require
from ..core.lifecycle import utc_now
from ..outputs import reject_links
from ..runtime import atomic_json, digest, read_json


def root(runtime, identity):
    require(
        isinstance(identity, str) and re.fullmatch(r"[0-9a-f]{64}", identity),
        "修訂提案識別無效",
    )
    folder = runtime.home / "graph_evolution" / identity
    reject_links(folder)
    return folder


def current(folder):
    versions = sorted(folder.glob("revision-*.json"))
    require(versions, "找不到修訂提案")
    return read_json(versions[-1])


def append(folder, body, phase):
    revision = body.get("revision", 0) + 1
    body = {**body, "revision": revision, "phase": phase, "updated_at": utc_now()}
    path = folder / f"revision-{revision:08}.json"
    require(not path.exists(), "修訂提案版本衝突")
    atomic_json(path, body)
    return body


def require_active_feedback(db, event):
    following = db.execute(
        "SELECT body FROM graph_feedback WHERE assessment_id=? AND revision=?",
        (event["assessment_id"], event["after"]["revision"] + 1),
    ).fetchone()
    require(
        not following or json.loads(following[0])["decision"] != "withdraw",
        "回饋已撤回",
    )


def proposed(service, session_id, feedback_ids, category, hypothesis, impact, risk, request_id):
    from ..graph.session import GraphSession

    require(
        category in {"performance", "profile", "logic", "goal"}
        and isinstance(feedback_ids, list)
        and 0 < len(feedback_ids) <= 50
        and all(isinstance(value, str) and value for value in feedback_ids)
        and all(isinstance(x, str) and x.strip() for x in (hypothesis, impact, risk, request_id)),
        "提案需要分類、回饋證據、原因假設、預期影響及風險",
    )
    rt = service.workspace.runtime
    session = GraphSession(rt, session_id)
    require(session.path.is_file(), "找不到執行紀錄")
    s = read_json(session.path)
    require(
        s["plan"]["format"] == 2 and not s.get("owner_session"),
        "請使用正式 Graph 主工作的紀錄",
    )
    rows = []
    with closing(rt.connect()) as db:
        for feedback_id in sorted(set(feedback_ids)):
            record = db.execute(
                """SELECT f.body,a.graph_hash FROM graph_feedback f JOIN graph_assessments a
                ON a.id=f.assessment_id WHERE f.id=? AND a.workflow=?""",
                (feedback_id, s["plan"]["workflow"]),
            ).fetchone()
            require(
                record and record["graph_hash"] == s["plan"]["graph_hash"],
                "回饋不屬於這個工作流版本",
            )
            event = json.loads(record["body"])
            require(event["decision"] != "withdraw", "撤回事件不能作為改善依據")
            require_active_feedback(db, event)
            rows.append(
                {
                    k: event[k]
                    for k in (
                        "feedback_id",
                        "assessment_id",
                        "decision",
                        "reason",
                        "scope",
                        "tags",
                        "before_hash",
                        "after_hash",
                    )
                }
            )
    task = service.workspace.task(s["plan"]["workflow"], read_only=True)
    require(task.manifest.version == s["plan"]["version"], "正式版本已變更，請先重新比對")
    identity = digest([session_id, request_id])
    body = {
        "proposal_id": identity,
        "session_id": session_id,
        "workflow_id": task.manifest.id,
        "base_version": task.manifest.version,
        "base_wheel_hash": task.manifest.sha256,
        "base_source_hash": s["plan"]["source_hash"],
        "base_graph_hash": s["plan"]["graph_hash"],
        "category": category,
        "evidence": rows,
        "hypothesis": hypothesis,
        "expected_impact": impact,
        "risk": risk,
    }
    with rt.lock("evolution:" + identity):
        folder = root(rt, identity)
        if folder.exists():
            first = read_json(folder / "revision-00000001.json")
            require(all(first[k] == v for k, v in body.items()), "同一提案請求已有不同內容")
            return current(folder)
        return append(folder, body, "proposed")
