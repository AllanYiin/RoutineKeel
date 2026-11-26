"""Durable operations for capability binding preparation and qualification."""

import hashlib

VERSION = 8
TABLES = {"binding_operations"}
STATEMENTS = (
    """CREATE TABLE binding_operations (
        work_id TEXT NOT NULL,
        operation_id TEXT NOT NULL,
        scope_hash TEXT NOT NULL,
        kind TEXT NOT NULL,
        dependencies TEXT NOT NULL,
        resource_key TEXT,
        replay_safe INTEGER NOT NULL CHECK(replay_safe IN (0, 1)),
        status TEXT NOT NULL CHECK(status IN (
            'pending', 'claimed', 'succeeded', 'failed', 'reconcile_required'
        )),
        generation INTEGER NOT NULL DEFAULT 0,
        lease_expires REAL,
        result TEXT,
        PRIMARY KEY (work_id, operation_id)
    )""",
    "CREATE INDEX binding_operation_resources ON binding_operations(status, resource_key, lease_expires)",
)
CHECKSUM = hashlib.sha256("\n".join(STATEMENTS).encode()).hexdigest()


def apply(db):
    for statement in STATEMENTS:
        db.execute(statement)
