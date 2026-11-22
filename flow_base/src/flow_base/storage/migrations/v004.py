"""v004: durable queue requests are distinct from execution Run identities."""
import hashlib

VERSION = 4
STATEMENTS = (
    '''CREATE TABLE execution_queue (
        execution_id TEXT PRIMARY KEY,
        workflow_id TEXT NOT NULL,
        flow_id TEXT NOT NULL,
        source TEXT NOT NULL,
        output TEXT NOT NULL,
        overrides_json TEXT NOT NULL,
        offline INTEGER NOT NULL CHECK(offline IN (0,1)),
        execution_spec_hash TEXT NOT NULL,
        idempotency_key TEXT UNIQUE,
        status TEXT NOT NULL CHECK(status IN
            ('queued','dispatching','running','cancel_requested','succeeded','failed','interrupted','cancelled')),
        run_id TEXT UNIQUE,
        submitted_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        worker_token TEXT,
        lease_expires_at REAL,
        error_code TEXT
    )''',
    'CREATE INDEX idx_execution_queue_status ON execution_queue(status, submitted_at)',
)
CHECKSUM = hashlib.sha256('\n'.join(STATEMENTS).encode()).hexdigest()


def apply(db):
    for statement in STATEMENTS:
        db.execute(statement)
