"""v003: connect step artifact sets to cache identity and attempt history."""
import hashlib

VERSION = 3
STATEMENTS = (
    'ALTER TABLE artifact_sets ADD COLUMN step_attempt_id TEXT',
    'ALTER TABLE artifact_sets ADD COLUMN cache_key TEXT',
    'CREATE INDEX IF NOT EXISTS idx_artifact_sets_cache ON artifact_sets(cache_key, created_at)',
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_delivery_run_active ON deliveries(run_id) WHERE status IN ('staged','published')",
)
CHECKSUM = hashlib.sha256('\n'.join(STATEMENTS).encode()).hexdigest()


def apply(db):
    for statement in STATEMENTS:
        db.execute(statement)
