"""Atomic Run state changes over SQLite; independent of CLI and workflow adapters."""
from __future__ import annotations

import time
import uuid
import hashlib
import json
from contextlib import closing
from datetime import datetime, timezone

from ..runtime import FlowError

TERMINAL = frozenset({'succeeded', 'failed', 'cancelled', 'interrupted'})
ACTIVE = frozenset({'created', 'queued', 'preparing', 'running', 'publishing', 'cancelling'})
TRANSITIONS = {
    'created': frozenset({'queued', 'failed', 'interrupted'}),
    'queued': frozenset({'preparing', 'cancelling', 'failed', 'interrupted'}),
    'preparing': frozenset({'running', 'cancelling', 'failed', 'interrupted'}),
    'running': frozenset({'publishing', 'cancelling', 'failed', 'interrupted'}),
    'publishing': frozenset({'succeeded', 'cancelling', 'failed', 'interrupted'}),
    'cancelling': frozenset({'cancelled', 'interrupted', 'failed'}),
    'succeeded': frozenset(),
    'failed': frozenset(),
    'cancelled': frozenset(),
    'interrupted': frozenset(),
}
ATTEMPT_TRANSITIONS = {
    'pending': frozenset({'running', 'cached', 'failed', 'cancelled', 'interrupted'}),
    'running': frozenset({'succeeded', 'failed', 'cancelled', 'interrupted'}),
    'cached': frozenset(),
    'succeeded': frozenset(),
    'failed': frozenset(),
    'cancelled': frozenset(),
    'interrupted': frozenset(),
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    def __init__(self, runtime):
        self.runtime = runtime

    def get(self, run_id):
        with closing(self.runtime.connect()) as db:
            row = db.execute('SELECT * FROM run_executions WHERE run_id=?', (run_id,)).fetchone()
            return dict(row) if row else None

    def create_attempt(self, run_id, step_id, attempt_no=1):
        attempt_id = 'attempt_' + uuid.uuid4().hex
        with closing(self.runtime.connect()) as db:
            with db:
                db.execute('INSERT INTO step_attempts(attempt_id,run_id,step_id,attempt_no,status) VALUES(?,?,?,?,?)',
                           (attempt_id, run_id, step_id, attempt_no, 'pending'))
        return attempt_id

    def set_attempt_cache_key(self, attempt_id, cache_key):
        with closing(self.runtime.connect()) as db:
            with db:
                updated = db.execute("UPDATE step_attempts SET cache_key=? WHERE attempt_id=? AND status='pending'",
                                     (cache_key, attempt_id))
                if updated.rowcount != 1:
                    raise FlowError('STATE_CONFLICT', 'Step Attempt 已離開 pending', attempt_id=attempt_id)

    def transition_attempt(self, attempt_id, expected, target, *, error_id=None):
        if target not in ATTEMPT_TRANSITIONS.get(expected, ()):
            raise FlowError('STATE_CONFLICT', 'Step Attempt 狀態轉移不合法', expected=expected, target=target)
        with closing(self.runtime.connect()) as db:
            with db:
                updated = db.execute('UPDATE step_attempts SET status=?,started_at=CASE WHEN ?=? THEN ? ELSE started_at END,ended_at=CASE WHEN ? THEN ? ELSE ended_at END,error_id=COALESCE(?,error_id) WHERE attempt_id=? AND status=?',
                                     (target, target, 'running', utc_now(), target in {'cached','succeeded','failed','cancelled','interrupted'},
                                      utc_now(), error_id, attempt_id, expected))
                if updated.rowcount != 1:
                    raise FlowError('STATE_CONFLICT', 'Step Attempt 已被其他程序更新', attempt_id=attempt_id)

    def finish_attempt_failure(self, attempt_id, *, interrupted=False, cancelled=False, error_id=None):
        if not attempt_id:
            return
        with closing(self.runtime.connect()) as db:
            row = db.execute('SELECT status FROM step_attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
        if row is None or row['status'] not in {'pending', 'running'}:
            return
        target = 'cancelled' if cancelled else ('interrupted' if interrupted else 'failed')
        self.transition_attempt(attempt_id, row['status'], target, error_id=error_id)

    def create(self, *, job_id, revision_id, spec_hash, idempotency_key=None, run_id=None,
               flow_id=None, source=None, output=None, revision_fingerprint=None, revision_version=None):
        """One writer per workspace; same idempotency key and spec returns same Run."""
        if not job_id or not revision_id or not spec_hash:
            raise FlowError('RUN_SPEC_INVALID', 'Run 需要工作、流程版本與執行規格指紋')
        self.runtime.initialize()
        with closing(self.runtime.connect()) as db:
            with db:
                db.execute('BEGIN IMMEDIATE')
                if idempotency_key is not None:
                    prior = db.execute('SELECT run_id,execution_spec_hash FROM run_executions WHERE job_id=? AND idempotency_key=?',
                                       (job_id, idempotency_key)).fetchone()
                    if prior:
                        if prior['execution_spec_hash'] == spec_hash:
                            return prior['run_id']
                        raise FlowError('IDEMPOTENCY_CONFLICT', '相同請求識別碼對應不同執行規格', job_id=job_id)
                busy = db.execute("SELECT run_id FROM run_executions WHERE execution_status IN ('created','queued','preparing','running','publishing','cancelling') LIMIT 1").fetchone()
                if busy:
                    raise FlowError('BUSY', '此工作區已有寫入型 Run', run_id=busy['run_id'])
                run_id = run_id or 'run_' + uuid.uuid4().hex
                if flow_id is not None:
                    now = utc_now()
                    db.execute('INSERT OR IGNORE INTO workflow_revisions VALUES(?,?,?,?,?,?)',
                               (revision_id, flow_id, revision_fingerprint or spec_hash,
                                revision_fingerprint or spec_hash, revision_version or '0.2', now))
                    db.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?,?,?,?,?)',
                               (job_id, flow_id, str(source or '工作'), '{}', None, now, now))
                    db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?,?,?)',
                               (run_id, flow_id, job_id, 'starting', str(source), str(output), time.time(), None, None))
                db.execute('INSERT INTO run_executions(run_id,job_id,revision_id,execution_spec_hash,execution_status,started_at,idempotency_key) VALUES(?,?,?,?,?,?,?)',
                           (run_id, job_id, revision_id, spec_hash, 'created', utc_now(), idempotency_key))
                return run_id

    def transition(self, run_id, expected, target, *, version=None, result_status=None, delivery_status=None, error_id=None):
        if target not in TRANSITIONS.get(expected, ()):
            raise FlowError('STATE_CONFLICT', 'Run 狀態轉移不合法', expected=expected, target=target)
        with closing(self.runtime.connect()) as db:
            with db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT execution_status,state_version FROM run_executions WHERE run_id=?', (run_id,)).fetchone()
                if row is None or row['execution_status'] != expected or (version is not None and row['state_version'] != version):
                    raise FlowError('STATE_CONFLICT', 'Run 狀態已變更', run_id=run_id, expected=expected)
                if target == 'succeeded':
                    delivery = db.execute("SELECT d.delivery_id,a.manifest_json,a.manifest_sha256 FROM deliveries d JOIN artifact_sets a ON a.artifact_set_id=d.artifact_set_id WHERE d.run_id=? AND d.status='published' LIMIT 1", (run_id,)).fetchone()
                    if delivery is None:
                        raise FlowError('DELIVERY_REQUIRED', 'Run 成功前必須有可驗證交付', run_id=run_id)
                    from pathlib import Path
                    manifest = Path(json.loads(delivery['manifest_json'])['path'])
                    if not manifest.is_file() or hashlib.sha256(manifest.read_bytes()).hexdigest() != delivery['manifest_sha256']:
                        raise FlowError('DELIVERY_INVALID', '正式交付清單遺失或內容已變更', run_id=run_id)
                ended = utc_now() if target in TERMINAL else None
                updated = db.execute('UPDATE run_executions SET execution_status=?,state_version=state_version+1, result_status=COALESCE(?,result_status),delivery_status=COALESCE(?,delivery_status),error_id=COALESCE(?,error_id),ended_at=COALESCE(?,ended_at) WHERE run_id=? AND execution_status=? AND state_version=?',
                                     (target, result_status, delivery_status, error_id, ended, run_id, expected, row['state_version']))
                if updated.rowcount != 1:
                    raise FlowError('STATE_CONFLICT', 'Run 狀態更新競爭失敗', run_id=run_id)
                if target in TERMINAL:
                    legacy_status = 'complete' if target == 'succeeded' else target
                    db.execute('UPDATE runs SET status=?,finished=?,error=COALESCE(?,error) WHERE run_id=?',
                               (legacy_status, time.time(), error_id, run_id))
                return row['state_version'] + 1

    def record_delivery(self, run_id, manifest_path):
        """Register an already-written result; Stage 7 will make file publication atomic."""
        path = manifest_path.resolve()
        content = path.read_bytes()
        artifact_set_id = 'artset_' + uuid.uuid4().hex
        delivery_id = 'delivery_' + uuid.uuid4().hex
        with closing(self.runtime.connect()) as db:
            with db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT job_id,execution_status FROM run_executions WHERE run_id=?', (run_id,)).fetchone()
                if row is None or row['execution_status'] != 'publishing':
                    raise FlowError('STATE_CONFLICT', '只能在發布階段登記交付', run_id=run_id)
                db.execute('INSERT INTO artifact_sets VALUES(?,?,?,?,?)',
                           (artifact_set_id, run_id, json.dumps({'path': str(path)}, ensure_ascii=False),
                            hashlib.sha256(content).hexdigest(), utc_now()))
                db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?)',
                           (delivery_id, row['job_id'], run_id, artifact_set_id, 'published', utc_now(), None))
        return delivery_id

    def finish_failure(self, run_id, *, interrupted=False, cancelled=False, error_id=None):
        row = self.get(run_id)
        if not row or row['execution_status'] in TERMINAL:
            return
        target = 'cancelled' if cancelled and row['execution_status'] == 'cancelling' else ('interrupted' if interrupted else 'failed')
        if row['execution_status'] == 'cancelling' and not (interrupted or cancelled):
            target = 'interrupted'
        self.transition(run_id, row['execution_status'], target, version=row['state_version'], error_id=error_id)

    def claim(self, run_id, *, token, pid, started_at, lease_seconds=20):
        if not token or not pid or started_at is None:
            raise FlowError('SUPERVISOR_IDENTITY', '認領需要 token、PID 與程序起始時間')
        with closing(self.runtime.connect()) as db:
            with db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT execution_status,state_version FROM run_executions WHERE run_id=?', (run_id,)).fetchone()
                if row is None or row['execution_status'] != 'queued':
                    raise FlowError('STATE_CONFLICT', '只有 queued Run 可被認領', run_id=run_id)
                updated = db.execute("UPDATE run_executions SET execution_status='preparing',state_version=state_version+1,supervisor_token=?,supervisor_pid=?,supervisor_started_at=?,heartbeat_at=?,lease_expires_at=? WHERE run_id=? AND execution_status='queued' AND state_version=?",
                                     (token, pid, str(started_at), utc_now(), str(time.time()+lease_seconds), run_id, row['state_version']))
                if updated.rowcount != 1:
                    raise FlowError('STATE_CONFLICT', 'Run 已被其他程序認領', run_id=run_id)
                return row['state_version'] + 1

    def heartbeat(self, run_id, token, *, lease_seconds=20):
        with closing(self.runtime.connect()) as db:
            with db:
                updated = db.execute("UPDATE run_executions SET heartbeat_at=?,lease_expires_at=? WHERE run_id=? AND supervisor_token=? AND execution_status IN ('preparing','running','publishing','cancelling')",
                                     (utc_now(), str(time.time()+lease_seconds), run_id, token))
                if updated.rowcount != 1:
                    raise FlowError('STATE_CONFLICT', 'Supervisor lease 已失效', run_id=run_id)

    def request_cancel(self, run_id):
        with closing(self.runtime.connect()) as db:
            with db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT execution_status,state_version FROM run_executions WHERE run_id=?', (run_id,)).fetchone()
                if row is None or 'cancelling' not in TRANSITIONS.get(row['execution_status'], ()):
                    raise FlowError('STATE_CONFLICT', 'Run 目前不可取消', run_id=run_id)
                db.execute("UPDATE run_executions SET execution_status='cancelling',state_version=state_version+1 WHERE run_id=? AND state_version=?",
                           (run_id, row['state_version']))
                if row['execution_status'] == 'queued':
                    db.execute("UPDATE run_executions SET execution_status='cancelled',state_version=state_version+1,ended_at=? WHERE run_id=? AND execution_status='cancelling'",
                               (utc_now(), run_id))
                    db.execute("UPDATE runs SET status='cancelled',finished=? WHERE run_id=?", (time.time(), run_id))
                    return row['state_version'] + 2
                return row['state_version'] + 1

    def reconcile(self, identity_matches, *, now=None):
        """Only a verified process identity can keep an expired lease active."""
        now = time.time() if now is None else now
        changed = []
        with closing(self.runtime.connect()) as db:
            cutoff = datetime.fromtimestamp(now - 20, timezone.utc).isoformat()
            pending = db.execute("SELECT 1 FROM run_executions WHERE (execution_status IN ('preparing','running','publishing','cancelling') AND CAST(lease_expires_at AS REAL) < ?) OR (execution_status IN ('created','queued') AND started_at < ?) LIMIT 1", (now, cutoff)).fetchone()
            if pending is None:
                return changed
            with db:
                db.execute('BEGIN IMMEDIATE')
                rows = db.execute("SELECT * FROM run_executions WHERE execution_status IN ('preparing','running','publishing','cancelling') AND CAST(lease_expires_at AS REAL) < ?", (now,)).fetchall()
                # A process may die between creating/queueing a Run and claiming
                # its supervisor. Do not leave that workspace BUSY forever.
                rows += db.execute("SELECT * FROM run_executions WHERE execution_status IN ('created','queued') AND started_at < ?", (cutoff,)).fetchall()
                for row in rows:
                    if row['execution_status'] not in {'created', 'queued'} and identity_matches(row['supervisor_pid'], row['supervisor_started_at'], row['supervisor_token']):
                        continue
                    updated = db.execute("UPDATE run_executions SET execution_status='interrupted',state_version=state_version+1,ended_at=? WHERE run_id=? AND state_version=?",
                                         (utc_now(), row['run_id'], row['state_version']))
                    if updated.rowcount == 1:
                        db.execute("UPDATE runs SET status='interrupted',finished=? WHERE run_id=?", (time.time(), row['run_id']))
                        changed.append(row['run_id'])
        return changed
