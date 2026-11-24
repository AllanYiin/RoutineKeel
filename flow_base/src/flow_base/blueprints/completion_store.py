"""Local, revisioned records for work deriving from an immutable blueprint."""

import json
from contextlib import closing

from ..runtime import FlowError, canonical


def _events(previous, state):
    known = {item["scope_hash"] for item in previous.get("gaps", [])}
    events = []
    for gap in state.get("gaps", []):
        if gap["scope_hash"] not in known:
            events.append(
                {"type": "gap_detected", "gap_id": gap["id"], "code": gap["code"]}
            )
            if state["status"] in {"needs_source", "needs_input"}:
                events.append(
                    {
                        "type": "input_requested",
                        "gap_id": gap["id"],
                        "actions": gap["actions"],
                    }
                )
    if state["status"] in {"qualifying", "trial_queued"}:
        events.append({"type": "action_started", "phase": state["status"]})
    for key, outcome in state.get("qualification", {}).items():
        report = outcome.get("report") or {}
        if report.get("verdict") == "PASS" and outcome != previous.get(
            "qualification", {}
        ).get(key):
            events.append(
                {
                    "type": "action_verified",
                    "node": key,
                    "request_hash": outcome["request_hash"],
                }
            )
    trial = state.get("trial", {})
    old_trial = previous.get("trial", {})
    if trial.get("outcome") and (trial.get("build_id"), trial["outcome"]) != (
        old_trial.get("build_id"),
        old_trial.get("outcome"),
    ):
        events.append(
            {
                "type": "trial_finished",
                "build_id": trial["build_id"],
                "outcome": trial["outcome"],
            }
        )
    if state["status"] in {"installed", "needs_update"} and state[
        "status"
    ] != previous.get("status"):
        events.append({"type": "install_rechecked", "status": state["status"]})
    return [
        {
            **event,
            "work_id": state["work_id"],
            "revision": state["revision"],
            "timestamp": state["updated_at"],
        }
        for event in events
    ]


class CompletionStore:
    def __init__(self, runtime):
        self.runtime = runtime

    def find(self, work_id):
        self.runtime.initialize()
        with closing(self.runtime.connect()) as db:
            row = db.execute(
                "SELECT body FROM blueprint_completion_revisions "
                "WHERE work_id=? ORDER BY revision DESC LIMIT 1",
                (work_id,),
            ).fetchone()
        return json.loads(row["body"]) if row else None

    def read(self, work_id):
        state = self.find(work_id)
        if state is None:
            raise FlowError("INVALID_CONFIGURATION", "找不到這項藍圖補強工作。")
        return state

    def replay(self, work_id, request_id, request_hash):
        with closing(self.runtime.connect()) as db:
            row = db.execute(
                "SELECT request_hash, body FROM blueprint_completion_revisions "
                "WHERE work_id=? AND request_id=?",
                (work_id, request_id),
            ).fetchone()
        if row and row["request_hash"] != request_hash:
            raise FlowError("REVISION_CONFLICT", "同一補強請求識別碼已用於其他內容。")
        return json.loads(row["body"]) if row else None

    def save(self, state, request_id, request_hash):
        with closing(self.runtime.connect()) as db, db:
            row = db.execute(
                "SELECT body FROM blueprint_completion_revisions WHERE work_id=? AND revision=?",
                (state["work_id"], state["revision"] - 1),
            ).fetchone()
            previous = json.loads(row["body"]) if row else {}
            state = {**state, "events": _events(previous, state)}
            db.execute(
                "INSERT INTO blueprint_completion_revisions VALUES(?,?,?,?,?)",
                (
                    state["work_id"],
                    state["revision"],
                    request_id,
                    request_hash,
                    canonical(state),
                ),
            )
        return state

    def history(self, work_id):
        self.read(work_id)
        with closing(self.runtime.connect()) as db:
            rows = db.execute(
                "SELECT body FROM blueprint_completion_revisions WHERE work_id=? ORDER BY revision",
                (work_id,),
            ).fetchall()
        return [json.loads(row["body"]) for row in rows]
