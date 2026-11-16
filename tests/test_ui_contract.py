"""Local desktop UI model and widget contract, without network services."""
import json
import shutil
import socket
import struct
import time
import tkinter as tk
import zlib

import pytest

from flow_base.core.lifecycle import RunStore
from flow_base.diagnostics import record_exception
from flow_base.runtime import FlowError
from flow_base.ui.app import DesktopApp
from flow_base.ui.model import LocalUIModel, error_card, fit_dimensions, parse_overrides, preview_file
from flow_base.workspace import Workspace
from test_workspace import workspace


def test_preview_reports_real_content_and_keeps_image_ratio(tmp_path):
    text = tmp_path / 'caption.srt'
    text.write_text('字幕測試', encoding='utf-8')
    viewed = preview_file(text)
    assert viewed.kind == 'text' and viewed.text == '字幕測試'
    assert viewed.size_bytes == text.stat().st_size
    video = tmp_path / 'clip.mp4'
    video.write_bytes(b'not a preview')
    unsupported = preview_file(video)
    assert unsupported.kind == 'unsupported' and unsupported.reason
    assert unsupported.text is None and unsupported.name == 'clip.mp4'
    assert fit_dimensions(1920, 1080, 320, 200) == (320, 180)
    assert fit_dimensions(100, 200, 200, 200) == (100, 200)
    with pytest.raises(ValueError):
        fit_dimensions(0, 100, 200, 200)


def test_ui_plan_run_reopen_project_and_find_old_artifacts(workspace):
    source = workspace / 'input.txt'
    source.write_text('desktop result', encoding='utf-8')
    output = workspace / 'result'
    model = LocalUIModel(workspace)
    assert any(item['id'] == 'ai_flow02' for item in model.workflows())
    assert model.history() == []
    with pytest.raises(FlowError) as bad_config:
        model.plan('ai_flow02', source, output, '[1, 2]')
    assert bad_config.value.code == 'INVALID_CONFIGURATION'
    planned = model.plan('ai_flow02', source, output, '{}', offline=True)
    assert planned.plan['ready_to_prepare']
    started = model.start()
    assert model.start().run_id == started.run_id
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        status = model.status()
        if status['execution_status'] in {'succeeded', 'failed', 'interrupted'}:
            break
        time.sleep(0.1)
    assert status['execution_status'] == 'succeeded'
    assert (output / 'artifacts/result.bin').read_text(encoding='utf-8') == 'desktop result'
    assert any(item['path'] == 'artifacts/result.bin' for item in model.artifacts())
    reopened = LocalUIModel(workspace)
    reopened.open_project(workspace)
    assert any(row['run_id'] == started.run_id for row in reopened.history())
    assert reopened.status(started.run_id)['execution_status'] == 'succeeded'
    assert reopened.artifacts(started.run_id)
    moved = workspace.parent / (workspace.name + '-moved')
    shutil.copytree(workspace, moved)
    relocated = LocalUIModel(moved)
    assert any(row['run_id'] == started.run_id for row in relocated.history())
    assert relocated.status(started.run_id)['execution_status'] == 'succeeded'
    assert relocated.artifacts(started.run_id)


def test_ui_error_card_cancel_and_scrubbed_export(workspace):
    assert parse_overrides('{"a": 1}') == {'a': 1}
    card = error_card(exception=FlowError('INPUT_NOT_FOUND', 'PRIVATE_MARKER'))
    assert card['title'] and 'PRIVATE_MARKER' not in json.dumps(card)
    ws = Workspace(workspace)
    task = ws.task('ai_flow02')
    run_id = ws.runtime.start_run(task.flow_id, 'fixture', workspace / 'result')
    store = RunStore(ws.runtime)
    store.transition(run_id, 'created', 'queued')
    store.claim(run_id, token='fixture', pid=123, started_at=1.0)
    model = LocalUIModel(workspace)
    requested = model.cancel(run_id)
    assert requested['status'] == 'cancelling' and not requested['confirmed_stopped']
    store.transition(run_id, 'cancelling', 'cancelled')
    record_exception(FlowError('INPUT_NOT_FOUND', 'PRIVATE_MARKER'),
                     ws.runtime.logs / task.flow_id / run_id,
                     runtime=ws.runtime, run_id=run_id, flow_id=task.flow_id)
    card = error_card(model.diagnostics(run_id))
    assert card['title'] and card['stage'] and card['next_action']
    target = workspace.parent / 'ui-diagnostics.json'
    exported = model.export_diagnostics(target, run_id)
    assert target.is_file() and exported
    assert 'PRIVATE_MARKER' not in target.read_text(encoding='utf-8')


def test_tk_window_renders_real_preview_and_does_not_connect_network(workspace, monkeypatch):
    def refuse_network(*_args, **_kwargs):
        raise AssertionError('UI startup must be offline')
    monkeypatch.setattr(socket.socket, 'connect', refuse_network)
    root = tk.Tk()
    root.withdraw()
    try:
        app = DesktopApp(workspace, root=root)
        png = workspace / 'picture.png'
        def chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
        png.write_bytes(b'\x89PNG\r\n\x1a\n' +
                        chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 1, 8, 2, 0, 0, 0)) +
                        chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00\x00\xff\x00')) +
                        chunk(b'IEND', b''))
        app._show_preview(png)
        assert app.preview.kind == 'image' and app.display_image is not None
        video = workspace / 'movie.mp4'
        video.write_bytes(b'unsupported')
        app._show_preview(video)
        assert app.preview.kind == 'unsupported'
        assert '無法預覽內容' in app.image_label.cget('text')
        assert str(app.plan_button['state']) == 'normal'
        source = workspace / 'input.txt'
        source.write_text('clicked through UI', encoding='utf-8')
        index = next(i for i, item in enumerate(app.available) if item['id'] == 'ai_flow02')
        app.workflow_list.selection_set(index)
        app._select_workflow()
        monkeypatch.setattr('flow_base.ui.app.filedialog.askopenfilename', lambda **_kwargs: str(source))
        app._choose_source()
        assert app.source_var.get() == str(source)
        app.output_var.set(str(workspace / 'clicked-result'))
        app._plan()
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline and app.model.plan_result is None:
            root.update()
            time.sleep(0.05)
        while time.monotonic() < deadline and str(app.start_button['state']) != 'normal':
            root.update()
            time.sleep(0.05)
        assert app.model.plan_result is not None
        assert str(app.start_button['state']) == 'normal'
        app._start()
        while time.monotonic() < deadline and not app.model.run_id:
            root.update()
            time.sleep(0.05)
        assert app.model.run_id
        while time.monotonic() < deadline:
            root.update()
            if (workspace / 'clicked-result/artifacts/result.bin').is_file():
                break
            time.sleep(0.05)
        assert (workspace / 'clicked-result/artifacts/result.bin').read_text(encoding='utf-8') == 'clicked through UI'
        while time.monotonic() < deadline and app.artifacts_list.size() == 0:
            root.update()
            time.sleep(0.05)
        assert app.artifacts_list.size() > 0
        assert app.model.status()['execution_status'] == 'succeeded'
        app._error(exc=FlowError('INPUT_NOT_FOUND', 'PRIVATE_MARKER'))
        rendered = app.error_text.get('1.0', 'end')
        assert '錯誤編號' in rendered and 'PRIVATE_MARKER' not in rendered
        app._refresh_history()
        history_index = next(i for i, row in enumerate(app.history)
                             if row['run_id'] == app.model.run_id)
        app.history_list.selection_set(history_index)
        app._select_history()
        assert app.artifacts_list.size() > 0
    finally:
        root.destroy()
