import json

from ..project.models import canonical_json
from ..project.service import now


class EventStore:
    def __init__(self, repository):
        self.repository = repository

    def append(self, project_id, call_id, event, payload, revision_id=None):
        with self.repository.transaction() as db:
            cursor = db.execute(
                """INSERT INTO studio_events
                (project_id,revision_id,call_id,event,payload_json,created_at) VALUES (?,?,?,?,?,?)""",
                (project_id, revision_id, call_id, event, canonical_json(payload), now()),
            )
            return {
                "sequence": cursor.lastrowid,
                "project_id": project_id,
                "revision_id": revision_id,
                "call_id": call_id,
                "event": event,
                **payload,
            }

    def read(self, project_id, after=0, limit=100):
        rows = self.repository.connection.execute(
            "SELECT * FROM studio_events WHERE project_id=? AND sequence>? ORDER BY sequence LIMIT ?",
            (project_id, after, max(1, min(limit, 1000))),
        )
        return [
            {
                "sequence": r["sequence"],
                "project_id": r["project_id"],
                "revision_id": r["revision_id"],
                "call_id": r["call_id"],
                "event": r["event"],
                **json.loads(r["payload_json"]),
            }
            for r in rows
        ]
