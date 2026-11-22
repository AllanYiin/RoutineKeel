"""Declared sidecars and multiple terminal outputs must be explicit and complete."""
import json
from pathlib import Path

import pytest
from flow_base.contracts import Step, Workflow
from flow_base.engine import Adapter, run_workflow
from flow_base.runtime import FlowError, Runtime
from flow_base.storage.artifacts import (
    build_artifact_set,
    gc_apply,
    gc_preview,
    validate_artifact_set,
)
from flow_base.workspace import Manifest, Workspace
from pydantic import ValidationError


def test_legacy_step_and_workflow_defaults_stay_compatible():
    workflow = Workflow(id='legacy', steps=[Step(id='one', handler='file.copy')])
    assert workflow.outputs == {}
    assert workflow.steps[0].cacheable is None
    assert workflow.steps[0].output_artifacts == []


def test_named_outputs_must_reference_terminal_steps():
    workflow = Workflow(id='multi', steps=[
        Step(id='source', handler='file.copy'),
        Step(id='left', handler='file.copy', needs=['source']),
        Step(id='right', handler='file.copy', needs=['source'])],
        outputs={'left_result': 'left', 'right_result': 'right'})
    assert set(workflow.outputs) == {'left_result', 'right_result'}
    with pytest.raises(ValidationError):
        Workflow(id='bad', steps=workflow.steps, outputs={'unsafe': 'source'})
    with pytest.raises(ValidationError):
        Workflow(id='bad', steps=workflow.steps, outputs={'../escape': 'left'})


def test_step_side_effects_and_artifact_paths_are_validated():
    with pytest.raises(ValidationError):
        Step(id='external', handler='file.copy', side_effects='external', cacheable=True)
    with pytest.raises(ValidationError):
        Step(id='external', handler='file.copy', side_effects='external', retry_policy='automatic')
    with pytest.raises(ValidationError):
        Step(id='bad', handler='file.copy', output_artifacts=['../secret.txt'])


def test_manifest_rejects_unsafe_named_output(workspace):
    task = Workspace(workspace).task('ai_flow02')
    data = task.manifest.model_dump()
    data['outputs'] = {'../escape': 'copy'}
    with pytest.raises(ValidationError):
        Manifest.model_validate(data)


def test_artifact_set_requires_all_sidecars_and_hashes(tmp_path):
    primary = tmp_path / 'result.json'
    sidecar = tmp_path / 'bundle' / 'audio.wav'
    sidecar.parent.mkdir()
    primary.write_text('{"result":true}', encoding='utf-8')
    sidecar.write_bytes(b'wave')
    manifest = build_artifact_set(primary, ['bundle/audio.wav'])
    assert manifest['primary'] == 'result.json'
    assert {item['path'] for item in manifest['files']} == {'result.json', 'bundle/audio.wav'}
    assert validate_artifact_set(primary, ['bundle/audio.wav']) == manifest
    sidecar.unlink()
    assert validate_artifact_set(primary, ['bundle/audio.wav']) is None
    with pytest.raises(FlowError) as error:
        build_artifact_set(primary, ['bundle/audio.wav'])
    assert error.value.code == 'ARTIFACT_MISSING'


def test_missing_manifest_or_changed_content_never_hits_cache(tmp_path):
    primary = tmp_path / 'result.json'
    primary.write_text('first', encoding='utf-8')
    assert validate_artifact_set(primary) is None
    build_artifact_set(primary)
    primary.write_text('changed', encoding='utf-8')
    assert validate_artifact_set(primary) is None
    primary.write_text('first', encoding='utf-8')
    (tmp_path / 'artifact-set.json').unlink()
    assert validate_artifact_set(primary) is None


def test_symlink_cannot_be_declared_as_artifact(tmp_path):
    outside = tmp_path.parent / 'outside-artifact.txt'
    outside.write_text('outside', encoding='utf-8')
    link = tmp_path / 'result.json'
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip('symlink creation unavailable')
    with pytest.raises(FlowError) as error:
        build_artifact_set(link)
    assert error.value.code in {'ARTIFACT_LINK', 'ARTIFACT_PATH'}


def test_declared_sidecar_loss_recomputes_in_real_workflow(workspace, monkeypatch):
    monkeypatch.setattr('flow_base.engine.require_space', lambda *args: None)
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    calls = []
    adapter = Adapter()
    def handler(ctx, step, dependencies, target):
        calls.append(ctx.run_id)
        target.write_text('result', encoding='utf-8')
        (target.parent / 'extra.txt').write_text('sidecar', encoding='utf-8')
        return target
    adapter.handlers = {'fixture.write': handler}
    monkeypatch.setattr(task, 'load', lambda runtime: adapter)
    workflow = Workflow(id='fixture', steps=[Step(id='write', handler='fixture.write',
                                                  output_artifacts=['extra.txt'])])
    source = workspace / 'input.txt'
    source.write_text('input', encoding='utf-8')
    output = workspace / 'result'
    first = run_workflow(ws.runtime, task, workflow, source, output)
    assert first['compatibility_warnings']
    second = run_workflow(ws.runtime, task, workflow, source, output)
    assert len(calls) == 1 and second['steps'][0]['status'] == 'cached'
    cache_key = first['steps'][0]['cache_key']
    with ws.runtime.connect() as db:
        rows = db.execute('SELECT run_id,step_attempt_id,cache_key FROM artifact_sets WHERE cache_key=?',
                          (cache_key,)).fetchall()
    assert {row['run_id'] for row in rows} == {first['run_id'], second['run_id']}
    assert all(row['step_attempt_id'].startswith('attempt_') for row in rows)
    sidecar = ws.runtime.cached(cache_key).parent / 'extra.txt'
    sidecar.unlink()
    third = run_workflow(ws.runtime, task, workflow, source, output)
    assert len(calls) == 2 and third['steps'][0]['status'] == 'executed'


def test_noncacheable_step_reexecutes_each_run(workspace, monkeypatch):
    monkeypatch.setattr('flow_base.engine.require_space', lambda *args: None)
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    calls = []
    adapter = Adapter()
    def handler(ctx, step, dependencies, target):
        calls.append(ctx.run_id)
        target.write_text(str(len(calls)), encoding='utf-8')
        return target
    adapter.handlers = {'fixture.write': handler}
    monkeypatch.setattr(task, 'load', lambda runtime: adapter)
    workflow = Workflow(id='fixture', steps=[Step(id='write', handler='fixture.write',
                                                  side_effects='local_write')])
    source = workspace / 'input.txt'
    source.write_text('input', encoding='utf-8')
    output = workspace / 'result'
    run_workflow(ws.runtime, task, workflow, source, output)
    second = run_workflow(ws.runtime, task, workflow, source, output)
    assert len(calls) == 2 and second['steps'][0]['status'] == 'executed'


def test_two_named_terminals_publish_complete_sets(workspace, monkeypatch):
    monkeypatch.setattr('flow_base.engine.require_space', lambda *args: None)
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    task.manifest.outputs = {'left_result': 'left', 'right_result': 'right'}
    adapter = Adapter()
    def handler(ctx, step, dependencies, target):
        label = step.parameters['label']
        target.write_text(label, encoding='utf-8')
        (target.parent / 'side.txt').write_text(label + '-side', encoding='utf-8')
        return target
    adapter.handlers = {'fixture.write': handler}
    monkeypatch.setattr(task, 'load', lambda runtime: adapter)
    workflow = Workflow(id='two-terminals', steps=[
        Step(id='left', handler='fixture.write', parameters={'label': 'left'}, output_artifacts=['side.txt']),
        Step(id='right', handler='fixture.write', parameters={'label': 'right'}, output_artifacts=['side.txt'])],
        outputs=task.manifest.outputs)
    source = workspace / 'input.txt'
    source.write_text('input', encoding='utf-8')
    output = workspace / 'result'
    report = run_workflow(ws.runtime, task, workflow, source, output)
    assert set(report['named_outputs']) == {'left_result', 'right_result'}
    assert 'compatibility_warnings' not in report
    assert (output / 'artifacts/left_result/result.json').read_text() == 'left'
    assert (output / 'artifacts/right_result/result.json').read_text() == 'right'
    assert (output / 'artifacts/left_result/side.txt').read_text() == 'left-side'
    index = json.loads((output / 'deliverables.json').read_text())
    paths = {item['path'] for item in index['files']}
    assert {'artifacts/left_result/result.json', 'artifacts/left_result/side.txt',
            'artifacts/right_result/result.json', 'artifacts/right_result/side.txt'} <= paths
    with ws.runtime.connect() as db:
        assert db.execute('SELECT count(*) FROM artifact_sets WHERE run_id=? AND step_attempt_id IS NOT NULL',
                          (report['run_id'],)).fetchone()[0] == 2


def test_named_outputs_require_installed_manifest_declaration(workspace, monkeypatch):
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    adapter = Adapter()
    adapter.handlers = {'fixture.write': lambda *args: None}
    monkeypatch.setattr(task, 'load', lambda runtime: adapter)
    workflow = Workflow(id='mismatch', steps=[Step(id='one', handler='fixture.write')], outputs={'result': 'one'})
    source = workspace / 'input.txt'
    source.write_text('input', encoding='utf-8')
    output = workspace / 'result'
    with pytest.raises(FlowError) as error:
        run_workflow(ws.runtime, task, workflow, source, output)
    assert error.value.code == 'PREFLIGHT_BLOCKED'
    assert not output.exists()


def test_gc_preview_and_explicit_apply_preserve_referenced_artifacts(tmp_path):
    runtime = Runtime(tmp_path / 'share_states')
    runtime.initialize()
    referenced = runtime.home / 'artifacts/ref/result.json'
    referenced.parent.mkdir(parents=True)
    referenced.write_text('keep', encoding='utf-8')
    runtime.remember('cache-ref', referenced)
    lineage = runtime.home / 'artifacts/lineage/result.json'
    lineage.parent.mkdir()
    lineage.write_text('lineage', encoding='utf-8')
    build_artifact_set(lineage)
    with runtime.connect() as db:
        db.execute('INSERT INTO artifact_sets(artifact_set_id,run_id,manifest_json,manifest_sha256,created_at,step_attempt_id,cache_key) VALUES(?,?,?,?,?,?,?)',
                   ('artset_lineage', 'run_old', json.dumps({'path': str(lineage.parent / 'artifact-set.json')}),
                    'hash', '2026-01-01', 'attempt_old', 'old-key'))
    orphan = runtime.home / 'artifacts/orphan/data.bin'
    orphan.parent.mkdir()
    orphan.write_bytes(b'orphan')
    preview = gc_preview(runtime)
    assert preview['bytes'] == len(b'orphan')
    assert {Path(item['path']).name for item in preview['candidates']} == {'orphan'}
    with pytest.raises(FlowError) as error:
        gc_apply(runtime)
    assert error.value.code == 'GC_CONFIRM_REQUIRED'
    result = gc_apply(runtime, confirm=True)
    assert result['bytes'] == len(b'orphan')
    assert not orphan.exists() and referenced.is_file() and lineage.is_file()
