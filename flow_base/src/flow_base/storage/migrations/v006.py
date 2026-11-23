"""Local, immutable Adapter qualification and environment lock evidence."""

import hashlib

VERSION = 6
TABLES = {
    "adapter_qualifications",
    "portable_binding_locks",
    "portable_binding_heads",
    "approved_degraded_blueprints",
}
STATEMENTS = (
    """CREATE TABLE adapter_qualifications(
        receipt TEXT PRIMARY KEY, graph_hash TEXT NOT NULL, node TEXT NOT NULL,
        verdict TEXT NOT NULL CHECK(verdict IN ('PASS','FAIL','NOT_RUN')),
        body TEXT NOT NULL, created_at TEXT NOT NULL)""",
    """CREATE TABLE portable_binding_locks(
        lock_hash TEXT PRIMARY KEY, graph_hash TEXT NOT NULL, body TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
    """CREATE TABLE portable_binding_heads(
        graph_hash TEXT PRIMARY KEY, lock_hash TEXT NOT NULL REFERENCES portable_binding_locks(lock_hash))""",
    """CREATE TABLE approved_degraded_blueprints(
        blueprint_hash TEXT PRIMARY KEY, approval_hash TEXT NOT NULL, body TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
    "CREATE INDEX adapter_qualification_graph ON adapter_qualifications(graph_hash,node)",
    *(
        f"""CREATE TRIGGER immutable_{table} BEFORE UPDATE ON {table}
        BEGIN SELECT RAISE(ABORT,'immutable binding evidence'); END"""
        for table in (
            "adapter_qualifications",
            "portable_binding_locks",
            "approved_degraded_blueprints",
        )
    ),
    *(
        f"""CREATE TRIGGER retain_{table} BEFORE DELETE ON {table}
        BEGIN SELECT RAISE(ABORT,'retain binding evidence'); END"""
        for table in (
            "adapter_qualifications",
            "portable_binding_locks",
            "approved_degraded_blueprints",
        )
    ),
)
CHECKSUM = hashlib.sha256("\n".join(STATEMENTS).encode()).hexdigest()


def apply(db):
    for statement in STATEMENTS:
        db.execute(statement)
