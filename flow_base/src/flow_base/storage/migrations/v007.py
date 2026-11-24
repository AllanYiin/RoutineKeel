"""Revision history for resumable blueprint completion."""

import hashlib

VERSION = 7
TABLES = {"blueprint_completion_revisions"}
STATEMENTS = (
    """CREATE TABLE blueprint_completion_revisions(
        work_id TEXT NOT NULL,
        revision INTEGER NOT NULL,
        request_id TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        body TEXT NOT NULL,
        PRIMARY KEY(work_id, revision),
        UNIQUE(work_id, request_id))""",
)
CHECKSUM = hashlib.sha256("\n".join(STATEMENTS).encode()).hexdigest()


def apply(db):
    for statement in STATEMENTS:
        db.execute(statement)
