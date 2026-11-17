"""The same Runtime may be used by interleaved Run and Action calls."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from flow_base.core.run_context import RunContext, activate, current
from flow_base.runtime import FlowError, Runtime


def test_hundred_interleaved_worker_contexts_do_not_cross(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path / 'share_states')
    runtime.logs = tmp_path / 'share_logs'
    (runtime.home / 'requests').mkdir(parents=True)
    python = tmp_path / 'python.exe'
    python.touch()
    runtime.workers['profile'] = tmp_path / 'worker.py'
    monkeypatch.setattr(runtime, 'environment', lambda profile: (tmp_path, python))
    observed = []

    def fake_process(argv, *, log, **kwargs):
        request = json.loads(Path(argv[2]).read_text(encoding='utf-8'))
        observed.append((request['run_id'], request['attempt_id'], log.parent.name, request['span_id']))
        Path(request['response_path']).write_text(json.dumps({'run_id': request['run_id']}), encoding='utf-8')

    monkeypatch.setattr('flow_base.runtime.run_process', fake_process)

    def invoke(index):
        run_id = f'run_{index}'
        receipts = []
        reporter = SimpleNamespace(progress=lambda *args: None,
                                   token_receipt=lambda *args, **kwargs: receipts.append((args, kwargs)))
        context = RunContext(runtime, run_id, runtime.logs / run_id, reporter,
                             attempt_id=f'attempt_{index}', action_payload={'run_id': run_id})
        with activate(context):
            assert current(runtime) is context
            assert runtime.action_context['run_id'] == run_id
            runtime.record_token_usage(f'request_{index}', {'input_tokens': index})
            assert runtime.worker('profile', {'value': index}) == {'run_id': run_id}
            assert receipts == [((f'request_{index}', {'input_tokens': index}), {'pending': False})]
        assert current(runtime) is None

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(invoke, range(100)))
    assert len(observed) == 100
    assert {(run, attempt, folder) for run, attempt, folder, _ in observed} == {
        (f'run_{i}', f'attempt_{i}', f'run_{i}') for i in range(100)}
    assert len({span_id for *_, span_id in observed}) == 100
    assert all(span_id.startswith('span_') for *_, span_id in observed)


def test_context_resets_on_failure_and_rejects_other_runtime(tmp_path):
    runtime = Runtime(tmp_path / 'one')
    other = Runtime(tmp_path / 'two')
    context = RunContext(runtime, 'run_one', tmp_path / 'logs', SimpleNamespace())
    with pytest.raises(RuntimeError, match='injected'):
        with activate(context):
            assert current(runtime) is context
            assert current(other) is None
            with pytest.raises(FlowError) as error:
                other.worker('profile', {}, context=context)
            assert error.value.code == 'RUN_CONTEXT_MISMATCH'
            raise RuntimeError('injected')
    assert current(runtime) is None
