"""One-shot read-only planner; the task wheel is extracted only to a temp dir."""
from __future__ import annotations

import argparse
import sqlite3
import tempfile
import traceback
from contextlib import closing
from pathlib import Path
from pydantic import ValidationError

from ..runtime import FlowError, Runtime, atomic_json, read_json
from ..workspace import Workspace


class ReadOnlyRuntime(Runtime):
    def initialize(self):
        return None

    def connect(self):
        database = self.home / 'state.sqlite3'
        if not database.is_file():
            raise FlowError('PLAN_NO_STATE', '尚無狀態資料庫；plan 不會建立資料庫')
        db = sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True)
        db.row_factory = sqlite3.Row
        return db

    def cached(self, key):
        if not (self.home / 'state.sqlite3').is_file():
            return None
        try:
            return super().cached(key)
        except sqlite3.Error:
            return None

    def _blocked(self, *_args, **_kwargs):
        raise FlowError('PLAN_SIDE_EFFECT', 'plan 只允許唯讀查詢，不可準備環境或執行 Worker')

    register_flow = start_run = update_run = remember = prepare_profile = worker = lock = _blocked


def probe(request, temporary):
    from ..engine import plan, _execution_identity, _execution_spec_hash
    from ..outputs import choose_output

    workspace = Workspace(Path(request['workspace']))
    workspace.runtime = ReadOnlyRuntime(workspace.root / 'share_states')
    workspace.runtime.logs = Path(temporary) / 'logs'
    task = workspace.task(request['task'], read_only=True)
    cache_root = Path(temporary) / 'plugins'
    cache_root.mkdir()
    task.load(workspace.runtime, cache_root=cache_root)
    workflow = task.configuration(workspace.runtime, request.get('overrides') or {})
    source = Path(request['source']) if request.get('source') else None
    output = Path(request['output']) if request.get('output') else None
    result = plan(workspace.runtime, task, workflow, source, output)
    resolved_output = choose_output(workspace.runtime, task, source, output)
    fingerprint = None
    if source is not None and source.is_file() and resolved_output is not None:
        _, input_hash, _ = _execution_identity(workspace.runtime, task, source, resolved_output)
        fingerprint = _execution_spec_hash(workspace.runtime, task, workflow, input_hash,
                                           resolved_output, bool(request.get('offline')))
    return {'plan': result, 'execution_spec_hash': fingerprint,
            'flow_id_status': task.flow_id_status,
            'estimates_status': 'declared' if result.get('estimates') else 'unknown'}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--response', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        request = read_json(args.request)
        with tempfile.TemporaryDirectory(prefix='routinekeel-plan-') as temporary:
            result = probe(request, temporary)
        atomic_json(args.response, {'ok': True, 'result': result})
        return 0
    except FlowError as exc:
        from ..diagnostics import redact
        atomic_json(args.response, {'ok': False, 'error': {'code': exc.code,
                    'message': redact(str(exc)), 'details': redact(exc.details)}})
        return 2
    except (ValidationError, ValueError, OSError, KeyError, ImportError) as exc:
        atomic_json(args.response, {'ok': False, 'error': {'code': 'INPUT_OR_ENVIRONMENT',
                    'message': '計畫輸入、流程設定或本機環境未通過檢查。',
                    'details': {'exception_type': type(exc).__name__}}})
        return 2
    except Exception as exc:
        atomic_json(args.response, {'ok': False, 'error': {'code': 'PLAN_PROBE_FAILED',
                    'message': '隔離計畫程序未能完成；請查本機診斷。',
                    'details': {'exception_type': type(exc).__name__,
                                'frames': [{'function': frame.name, 'line': frame.lineno}
                                           for frame in traceback.extract_tb(exc.__traceback__)]}}})
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
