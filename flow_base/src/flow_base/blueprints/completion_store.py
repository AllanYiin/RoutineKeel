"""Local, revisioned records for work deriving from an immutable blueprint."""

import json
import sqlite3
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
    if state["status"] in {"preparing", "qualifying", "trial_queued"}:
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
    for key, preparation in state.get("preparations", {}).items():
        if preparation.get("status") == "prepared" and preparation != previous.get("preparations", {}).get(key):
            events.append({"type": "action_verified", "node": key, "operation_id": preparation["operation_id"]})
    for key, operation in state.get("host_operations", {}).items():
        status = operation["status"]
        if status != previous.get("host_operations", {}).get(key, {}).get("status"):
            events.append({"type": "host_preparation", "operation_id": key, "status": status})
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

    def for_draft(self, draft_id, intent):
        """Return the latest non-retired work per identity, including revised drafts."""
        database = self.runtime.home / "state.sqlite3"
        if not database.is_file():
            return []
        with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=30)) as db:
            db.row_factory = sqlite3.Row
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='blueprint_completion_revisions' AND type='table'").fetchone() is None:
                return []
            rows = db.execute(
                """SELECT r.body FROM blueprint_completion_revisions r
                   JOIN (SELECT work_id, MAX(revision) AS revision
                         FROM blueprint_completion_revisions GROUP BY work_id) latest
                   USING (work_id, revision)
                   WHERE (json_extract(r.body, '$.draft_id') = ?
                          OR json_extract(r.body, '$.source_draft_id') = ?)
                     AND json_extract(r.body, '$.intent') = ?
                     AND json_extract(r.body, '$.status') NOT IN ('archived', 'cancelled')
                   ORDER BY json_extract(r.body, '$.updated_at') DESC, r.work_id
                   LIMIT 20""",
                (draft_id, draft_id, intent),
            ).fetchall()
        fields = ("work_id", "draft_id", "source_draft_id", "workflow_id",
                  "status", "revision", "intent", "updated_at")
        return [
            {**{field: state.get(field) for field in fields},
             "selected_tools": sorted(state.get("binding_selections", {})),
             "case_strategy": state.get("case_strategy", {}).get("mode")}
            for state in (json.loads(row["body"]) for row in rows)
        ]

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
