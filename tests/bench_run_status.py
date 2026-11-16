"""Reproducible M7 local read benchmark; run outside pytest on the target machine."""
from __future__ import annotations

import argparse
import gc
import json
import os
import platform
import statistics
import tempfile
import time
from contextlib import closing
from pathlib import Path

from flow_base.core.service import WorkflowService
from flow_base.workspace import Workspace


def measure(root: Path, runs: int, samples: int) -> dict:
    if runs < 2 or samples < 1:
        raise ValueError('runs >= 2 and samples >= 1 are required')
    with tempfile.TemporaryDirectory(prefix='routinekeel-m7-', dir=root) as temporary:
        workspace = Workspace(Path(temporary))
        runtime = workspace.runtime
        flow_id = runtime.register_flow('m7-fixture', '1', 'fixture', 'M7 fixture')
        seed = runtime.start_run(flow_id, 'input', 'output')
        with closing(runtime.connect()) as db:
            db.execute('BEGIN IMMEDIATE')
            for index in range(1, runs):
                run_id = f'run_{index:032x}'
                db.execute('INSERT INTO runs SELECT ?,flow_id,job_id,status,source,output,started,finished,error '
                           'FROM runs WHERE run_id=?', (run_id, seed))
                db.execute('INSERT INTO run_executions SELECT ?,job_id,revision_id,execution_spec_hash,'
                           'execution_status,result_status,delivery_status,state_version,supervisor_token,'
                           'worker_pid,worker_started_at,lease_expires_at,started_at,ended_at,error_id,'
                           'idempotency_key,supervisor_pid,supervisor_started_at,heartbeat_at '
                           'FROM run_executions WHERE run_id=?', (run_id, seed))
            db.commit()
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        service = WorkflowService(temporary)
        elapsed = []
        for index in range(samples):
            run_id = f'run_{((index * 23) % (runs - 1) + 1):032x}'
            started = time.perf_counter()
            service.get_run_status(run_id)
            elapsed.append((time.perf_counter() - started) * 1000)
        elapsed.sort()
        result = {'runs': runs, 'samples': samples,
                  'p50_ms': round(statistics.median(elapsed), 2),
                  'p95_ms': round(elapsed[max(0, int(samples * 0.95 + 0.9999) - 1)], 2),
                  'max_ms': round(elapsed[-1], 2),
                  'under_200_ms': elapsed[max(0, int(samples * 0.95 + 0.9999) - 1)] < 200,
                  'platform': platform.platform(), 'processor': platform.processor(),
                  'logical_cpus': os.cpu_count(), 'python': platform.python_version()}
        del service, workspace, runtime, db
        gc.collect()
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    parser.add_argument('--runs', type=int, default=5000)
    parser.add_argument('--samples', type=int, default=200)
    args = parser.parse_args()
    result = measure(args.root.resolve(), args.runs, args.samples)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['under_200_ms'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
