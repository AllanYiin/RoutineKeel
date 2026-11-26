"""Persistent, scoped operations for blueprint binding workers."""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass

from ..diagnostics import redact
from ..runtime import FlowError, canonical, digest


@dataclass(frozen=True)
class BindingOperation:
    kind: str
    scope_hash: str
    dependencies: tuple[str, ...] = ()
    resource_key: str | None = None
    replay_safe: bool = True

    @property
    def id(self):
        return digest([self.kind, self.scope_hash, self.dependencies, self.resource_key, self.replay_safe])


@dataclass(frozen=True)
class BindingLease:
    operation_id: str
    generation: int


class BindingOperations:
    def __init__(self, runtime, work_id):
        self.runtime = runtime
        self.work_id = work_id

    def register(self, operations):
        self.runtime.initialize()
        with closing(self.runtime.connect()) as db, db:
            db.executemany(
                """INSERT INTO binding_operations
                   (work_id, operation_id, scope_hash, kind, dependencies, resource_key, replay_safe, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'pending') ON CONFLICT(work_id, operation_id) DO NOTHING""",
                [
                    (self.work_id, item.id, item.scope_hash, item.kind, canonical(item.dependencies),
                     item.resource_key, int(item.replay_safe))
                    for item in operations
                ],
            )

    def _expire(self, db, now):
        db.execute(
            """UPDATE binding_operations
               SET status = CASE WHEN replay_safe = 1 THEN 'pending' ELSE 'reconcile_required' END,
                   lease_expires = NULL
               WHERE status = 'claimed' AND lease_expires <= ?""",
            (now,),
        )

    def _work_stopped(self, db):
        state = db.execute(
            """SELECT json_extract(body, '$.status') FROM blueprint_completion_revisions
               WHERE work_id = ? ORDER BY revision DESC LIMIT 1""",
            (self.work_id,),
        ).fetchone()
        return bool(state and state[0] in {"paused", "cancelled", "archived"})

    def work_stopped(self):
        with closing(self.runtime.connect()) as db:
            return self._work_stopped(db)

    def claim(self, operation_id, *, slots=4, lease_seconds=180, retry_failed=False):
        now = time.time()
        with closing(self.runtime.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db, now)
            if self._work_stopped(db):
                return None
            row = db.execute(
                "SELECT * FROM binding_operations WHERE work_id = ? AND operation_id = ?",
                (self.work_id, operation_id),
            ).fetchone()
            if row is None or (
                row["status"] != "pending" and not (
                    retry_failed and row["status"] == "failed" and row["replay_safe"]
                )
            ):
                return None
            dependencies = json.loads(row["dependencies"])
            if any(
                not db.execute(
                    """SELECT 1 FROM binding_operations
                       WHERE work_id = ? AND operation_id = ? AND status = 'succeeded'""",
                    (self.work_id, dependency),
                ).fetchone()
                for dependency in dependencies
            ):
                return None
            active = db.execute(
                "SELECT COUNT(*) FROM binding_operations WHERE work_id = ? AND status = 'claimed'",
                (self.work_id,),
            ).fetchone()[0]
            resource = row["resource_key"]
            if active >= slots or (resource and db.execute(
                """SELECT 1 FROM binding_operations
                   WHERE resource_key = ? AND status IN ('claimed', 'reconcile_required') LIMIT 1""",
                (resource,),
            ).fetchone()):
                return None
            generation = row["generation"] + 1
            db.execute(
                """UPDATE binding_operations SET status = 'claimed', generation = ?, lease_expires = ?, result = NULL
                   WHERE work_id = ? AND operation_id = ?""",
                (generation, now + lease_seconds, self.work_id, operation_id),
            )
            return BindingLease(operation_id, generation)

    def finish(self, lease, *, succeeded, result):
        with closing(self.runtime.connect()) as db, db:
            row = db.execute(
                """UPDATE binding_operations SET status = ?, result = ?, lease_expires = NULL
                   WHERE work_id = ? AND operation_id = ? AND generation = ?
                     AND status = 'claimed' AND lease_expires > ?""",
                ("succeeded" if succeeded else "failed", canonical(result), self.work_id,
                 lease.operation_id, lease.generation, time.time()),
            )
            if row.rowcount != 1:
                raise FlowError("REVISION_CONFLICT", "綁定子工作認領已過期或變更，結果未被採用。")

    def results(self):
        with closing(self.runtime.connect()) as db:
            rows = db.execute(
                """SELECT operation_id, kind, status, scope_hash, generation, result
                   FROM binding_operations WHERE work_id = ? ORDER BY kind, operation_id""",
                (self.work_id,),
            ).fetchall()
        return [
            {**dict(row), "result": json.loads(row["result"]) if row["result"] else None}
            for row in rows
        ]

    def invalidate(self, operation_id):
        """A previously validated local artifact is no longer present."""
        with closing(self.runtime.connect()) as db, db:
            db.execute(
                """UPDATE binding_operations SET status = 'pending', result = NULL
                   WHERE work_id = ? AND operation_id = ? AND status = 'succeeded' AND replay_safe = 1""",
                (self.work_id, operation_id),
            )

    def run(self, jobs, *, slots=None, retry_failed=False):
        """Run a bounded batch and return available results in registration order."""
        if not jobs:
            return {}
        if slots is None:
            configured = os.environ.get("ROUTINEKEEL_BINDING_SLOTS", "4")
            if configured not in {"1", "2", "3", "4"}:
                raise FlowError("INVALID_CONFIGURATION", "ROUTINEKEEL_BINDING_SLOTS 必須為 1 到 4。")
            slots = int(configured)
        self.register([operation for operation, _action, _check in jobs])

        def execute(job):
            operation, action, succeeded = job
            lease = self.claim(operation.id, slots=slots, retry_failed=retry_failed, lease_seconds=300)
            if lease is None:
                return False
            try:
                result = action()
            except FlowError as exc:
                self.finish(lease, succeeded=False, result={"operation_error": exc.as_dict()["error"]})
                return True
            except (OSError, ValueError) as exc:
                error = FlowError(
                    "TOOLCHAIN_PREPARATION_BLOCKED", "無法安全完成綁定子工作。",
                    reason=redact(str(exc)), kind=operation.kind,
                )
                self.finish(lease, succeeded=False, result={"operation_error": error.as_dict()["error"]})
                return True
            self.finish(lease, succeeded=succeeded(result), result=result)
            return True

        remaining = list(jobs)
        while remaining:
            concurrent = [job for job in remaining if job[0].resource_key is None]
            exclusive = [job for job in remaining if job[0].resource_key is not None]
            ran = []
            if concurrent:
                with ThreadPoolExecutor(max_workers=min(slots, len(concurrent))) as pool:
                    ran.extend(zip(concurrent, pool.map(execute, concurrent), strict=True))
            ran.extend((job, execute(job)) for job in exclusive)
            remaining = [job for job, claimed in ran if not claimed]
            if len(remaining) == len(ran):
                break
        requested = {operation.id for operation, _action, _check in jobs}
        return {row["operation_id"]: row["result"] for row in self.results()
                if row["operation_id"] in requested and row["result"] is not None}
