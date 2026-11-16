"""SQLite backed execution requests, independent of MCP connection lifetime."""
from __future__ import annotations

import json
import re
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone

from ..runtime import FlowError

EXECUTION_ID = re.compile(r'^execution_[a-f0-9]{32}$')
TERMINAL = frozenset({'succeeded', 'failed', 'interrupted', 'cancelled'})


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class QueueStore:
    def __init__(self, runtime):
        self.runtime = runtime

    def submit(self, *, workflow_id, flow_id, source, output, overrides,
               offline, spec_hash, idempotency_key=None):
        self.runtime.initialize()
        encoded = json.dumps(overrides, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
        with closing(self.runtime.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            if idempotency_key is not None:
                previous = db.execute('SELECT * FROM execution_queue WHERE idempotency_key=?',
                                      (idempotency_key,)).fetchone()
                if previous is not None:
                    if (previous['workflow_id'] != workflow_id or previous['source'] != str(source) or
                            previous['output'] != str(output) or previous['overrides_json'] != encoded or
                            previous['offline'] != int(offline) or
                            previous['execution_spec_hash'] != spec_hash):
                        db.rollback()
                        raise FlowError('IDEMPOTENCY_CONFLICT', '相同提交鍵的工作規格不同')
                    db.commit()
                    return dict(previous)
            execution_id = 'execution_' + uuid.uuid4().hex
            db.execute('INSERT INTO execution_queue('
                       'execution_id,workflow_id,flow_id,source,output,overrides_json,offline,'
                       'execution_spec_hash,idempotency_key,status,submitted_at) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (execution_id, workflow_id, flow_id, str(source), str(output), encoded,
                        int(offline), spec_hash, idempotency_key, 'queued', utc_now()))
            row = db.execute('SELECT * FROM execution_queue WHERE execution_id=?',
                             (execution_id,)).fetchone()
            db.commit()
            return dict(row)

    def get(self, execution_id):
        if not isinstance(execution_id, str) or not EXECUTION_ID.fullmatch(execution_id):
            raise FlowError('EXECUTION_UNKNOWN', '請提供有效的 execution_id')
        if not (self.runtime.home / 'state.sqlite3').is_file():
            raise FlowError('EXECUTION_UNKNOWN', '找不到指定的待執行工作')
        self.runtime.initialize()
        with closing(self.runtime.connect()) as db:
            row = db.execute('SELECT * FROM execution_queue WHERE execution_id=?',
                             (execution_id,)).fetchone()
        if row is None:
            raise FlowError('EXECUTION_UNKNOWN', '找不到指定的待執行工作')
        return dict(row)

    def next_item(self):
        with closing(self.runtime.connect()) as db:
            row = db.execute("SELECT * FROM execution_queue WHERE status IN "
                             "('dispatching','running','cancel_requested','queued') "
                             "ORDER BY CASE status WHEN 'running' THEN 0 "
                             "WHEN 'cancel_requested' THEN 0 WHEN 'dispatching' THEN 1 ELSE 2 END, "
                             "submitted_at LIMIT 1").fetchone()
        return dict(row) if row else None

    def claim(self, execution_id, token):
        with closing(self.runtime.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM execution_queue WHERE execution_id=?',
                             (execution_id,)).fetchone()
            if row is None:
                db.rollback()
                raise FlowError('EXECUTION_UNKNOWN', '找不到指定的待執行工作')
            if row['status'] not in {'queued', 'dispatching'}:
                db.rollback()
                return None
            db.execute("UPDATE execution_queue SET status='dispatching',worker_token=?,"
                       "lease_expires_at=? WHERE execution_id=?",
                       (token, time.time() + 30, execution_id))
            db.commit()
            return dict(row)

    def heartbeat(self, execution_id, token):
        with closing(self.runtime.connect()) as db:
            with db:
                db.execute("UPDATE execution_queue SET lease_expires_at=? WHERE execution_id=? "
                           "AND worker_token=? AND status IN ('dispatching','running','cancel_requested')",
                           (time.time() + 30, execution_id, token))

    def mark_running(self, execution_id, token, run_id):
        with closing(self.runtime.connect()) as db:
            with db:
                updated = db.execute("UPDATE execution_queue SET run_id=?,"
                                     "status=CASE WHEN status='cancel_requested' THEN status ELSE 'running' END,"
                                     "started_at=COALESCE(started_at,?),lease_expires_at=? "
                                     "WHERE execution_id=? AND worker_token=? "
                                     "AND status IN ('dispatching','cancel_requested')",
                                     (run_id, utc_now(), time.time() + 30, execution_id, token))
                if updated.rowcount != 1:
                    raise FlowError('STATE_CONFLICT', '佇列工作在啟動時已變更')

    def mark_terminal(self, execution_id, status, *, error_code=None):
        if status not in TERMINAL:
            raise FlowError('STATE_CONFLICT', '佇列終態不合法')
        with closing(self.runtime.connect()) as db:
            with db:
                db.execute("UPDATE execution_queue SET status=?,finished_at=?,lease_expires_at=NULL,"
                           "error_code=? WHERE execution_id=? AND status NOT IN "
                           "('succeeded','failed','interrupted','cancelled')",
                           (status, utc_now(), error_code, execution_id))

    def requeue(self, execution_id, token):
        with closing(self.runtime.connect()) as db:
            with db:
                db.execute("UPDATE execution_queue SET status='queued',worker_token=NULL,"
                           "lease_expires_at=NULL WHERE execution_id=? AND worker_token=? "
                           "AND status='dispatching' AND run_id IS NULL", (execution_id, token))

    def cancel(self, execution_id):
        self.get(execution_id)
        with closing(self.runtime.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM execution_queue WHERE execution_id=?',
                             (execution_id,)).fetchone()
            status = row['status']
            if status == 'queued':
                db.execute("UPDATE execution_queue SET status='cancelled',finished_at=?,"
                           "lease_expires_at=NULL WHERE execution_id=?", (utc_now(), execution_id))
            elif status in {'dispatching', 'running'}:
                db.execute("UPDATE execution_queue SET status='cancel_requested' "
                           "WHERE execution_id=?", (execution_id,))
            db.commit()
            updated = db.execute('SELECT * FROM execution_queue WHERE execution_id=?',
                                 (execution_id,)).fetchone()
            return dict(updated)
