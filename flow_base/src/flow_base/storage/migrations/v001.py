"""v001: preserve legacy tables and add the reliability schema."""
import hashlib

VERSION = 1
LEGACY_COLUMNS = {
    'steps': {'cache_key', 'status', 'artifact', 'artifact_hash', 'updated'},
    'requests': {'id', 'job', 'cache_key', 'reserved', 'charged', 'status', 'response', 'created'},
    'flows': {'flow_id', 'task_id', 'version', 'fingerprint', 'title', 'created', 'updated'},
    'runs': {'run_id', 'flow_id', 'job_id', 'status', 'source', 'output', 'started', 'finished', 'error'},
}
NEW_TABLES = {'schema_migrations', 'workflow_revisions', 'jobs', 'run_executions',
              'step_attempts', 'artifact_sets', 'deliveries', 'diagnostics', 'event_index'}
STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS steps(cache_key TEXT PRIMARY KEY, status TEXT NOT NULL, artifact TEXT, artifact_hash TEXT, updated REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, job TEXT NOT NULL, cache_key TEXT NOT NULL, reserved INTEGER NOT NULL, charged INTEGER, status TEXT NOT NULL, response TEXT, created REAL NOT NULL)",
    "CREATE INDEX IF NOT EXISTS requests_job ON requests(job)",
    "CREATE TABLE IF NOT EXISTS flows(flow_id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE, version TEXT NOT NULL, fingerprint TEXT NOT NULL, title TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL)",
    "CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, flow_id TEXT NOT NULL REFERENCES flows(flow_id), job_id TEXT, status TEXT NOT NULL, source TEXT, output TEXT, started REAL NOT NULL, finished REAL, error TEXT)",
    "CREATE INDEX IF NOT EXISTS runs_flow ON runs(flow_id, started)",
    "CREATE TABLE IF NOT EXISTS workflow_revisions(revision_id TEXT PRIMARY KEY, flow_id TEXT NOT NULL, manifest_hash TEXT NOT NULL, package_hash TEXT NOT NULL, contract_version TEXT NOT NULL, created_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS jobs(job_id TEXT PRIMARY KEY, flow_id TEXT NOT NULL, title TEXT NOT NULL, settings_json TEXT NOT NULL, deleted_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS run_executions(run_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, revision_id TEXT NOT NULL, execution_spec_hash TEXT NOT NULL, execution_status TEXT NOT NULL, result_status TEXT, delivery_status TEXT, state_version INTEGER NOT NULL DEFAULT 0, supervisor_token TEXT, worker_pid INTEGER, worker_started_at TEXT, lease_expires_at TEXT, started_at TEXT NOT NULL, ended_at TEXT, error_id TEXT)",
    "CREATE INDEX IF NOT EXISTS idx_run_exec_job ON run_executions(job_id, started_at)",
    "CREATE TABLE IF NOT EXISTS step_attempts(attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, step_id TEXT NOT NULL, attempt_no INTEGER NOT NULL, status TEXT NOT NULL, cache_key TEXT, started_at TEXT, ended_at TEXT, error_id TEXT, UNIQUE(run_id,step_id,attempt_no))",
    "CREATE TABLE IF NOT EXISTS artifact_sets(artifact_set_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, manifest_json TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, created_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS deliveries(delivery_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, run_id TEXT NOT NULL, artifact_set_id TEXT NOT NULL, status TEXT NOT NULL, published_at TEXT, superseded_by TEXT)",
    "CREATE TABLE IF NOT EXISTS diagnostics(error_id TEXT PRIMARY KEY, run_id TEXT, step_attempt_id TEXT, code TEXT NOT NULL, cause_status TEXT NOT NULL, diagnostic_path TEXT, occurred_at TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS event_index(event_id TEXT PRIMARY KEY, run_id TEXT, step_attempt_id TEXT, event_type TEXT NOT NULL, timestamp_utc TEXT NOT NULL, file_path TEXT NOT NULL, offset_bytes INTEGER NOT NULL)",
    "CREATE VIEW IF NOT EXISTS legacy_run_status AS SELECT run_id, status AS original_status, CASE status WHEN 'starting' THEN 'preparing' WHEN 'waiting' THEN 'queued' WHEN 'complete' THEN 'succeeded' ELSE status END AS inferred_execution_status FROM runs",
)
CHECKSUM = hashlib.sha256('\n'.join(STATEMENTS).encode()).hexdigest()


def apply(db):
    for statement in STATEMENTS:
        db.execute(statement)
