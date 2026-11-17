"""v002: fields needed for atomic claims, idempotency and supervisor leases."""
import hashlib

VERSION = 2
STATEMENTS = (
    'ALTER TABLE run_executions ADD COLUMN idempotency_key TEXT',
    'ALTER TABLE run_executions ADD COLUMN supervisor_pid INTEGER',
    'ALTER TABLE run_executions ADD COLUMN supervisor_started_at TEXT',
    'ALTER TABLE run_executions ADD COLUMN heartbeat_at TEXT',
    'CREATE UNIQUE INDEX IF NOT EXISTS idx_run_idempotency ON run_executions(job_id, idempotency_key) WHERE idempotency_key IS NOT NULL',
    'CREATE INDEX IF NOT EXISTS idx_run_execution_status ON run_executions(execution_status, lease_expires_at)',
)
CHECKSUM = hashlib.sha256('\n'.join(STATEMENTS).encode()).hexdigest()


def apply(db):
    for statement in STATEMENTS:
        db.execute(statement)
