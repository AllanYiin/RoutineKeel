import os
import socket
import time
from pathlib import Path

import pytest
from compiler_helpers import runtime_python
from workflow_studio.contracts import StudioError
from workflow_studio.ui.model import StudioUIModel, analysis_text

pytest.importorskip("PIL", reason="Desktop Studio requires its declared image dependencies")


def case_folder(tmp_path):
    folder = tmp_path / "成功案例"
    (folder / "input").mkdir(parents=True)
    (folder / "output").mkdir()
    (folder / "input/a.txt").write_text("一份成功範例", encoding="utf-8")
    (folder / "output/a.txt").write_text("一份成功範例", encoding="utf-8")
    return folder


def capture_virtual_window(root, name):
    if os.environ.get("STUDIO_HEADLESS_UI_TESTS") == "1" and os.environ.get(
        "STUDIO_GATE_REPORT_DIR"
    ):
        from test_ui_guidance import capture_window

        if root.winfo_exists() and root.winfo_ismapped():
            capture_window(root, Path(os.environ["STUDIO_GATE_REPORT_DIR"]) / name)


def destroy_test_window(root):
    # Each test owns its interpreter; don't leak polling callbacks into the next Tk root.
    for pending in root.tk.splitlist(root.tk.call("after", "info")):
        root.after_cancel(pending)
    root.destroy()


def test_model_end_to_end_without_editing_code_or_configuration(tmp_path):
    model = StudioUIModel(tmp_path / "studio", runtime_python=runtime_python())
    p = model.create_project("複製流程", "將檔案完整複製", "輸出內容與正確成果相同")
    source = model.import_path(case_folder(tmp_path))
    candidates, _ = model.folder_candidates(source.source_id)
    assert candidates
    revision = model.accept_candidate(0)
    assert revision.status == "ir_ready"
    node = revision.ir["nodes"][0]
    changed = model.edit_node(node["id"], output_name="我的成果.txt")
    assert changed.ir["revision"] == 2
    with pytest.raises(StudioError):
        model.edit_node(node["id"], delete=True)
    report = model.verify()
    assert report["status"] == "passed", report
    result = model.approve_publish(tmp_path / "installed", "測試者", "已核對試做成果")
    assert (tmp_path / "installed" / result["workflow_id"] / "flow.json").exists()
    reopened = StudioUIModel(tmp_path / "studio", runtime_python=runtime_python())
    reopened.select_project(p.project_id)
    assert reopened.revision().ir == changed.ir
    assert reopened.release_history()


def test_analysis_text_is_visible_without_ir_json():
    assert analysis_text('{"analysis":"逐步分析') == "逐步分析"
    assert analysis_text('{"analysis":"完成","ir":{"nodes":[]}}') == "完成"
    assert analysis_text('{"analysis":"hello\\u4e') == "hello"


def test_model_configuration_reopens_without_storing_credentials(tmp_path):
    model = StudioUIModel(tmp_path)
    project = model.create_project("設定", "描述工作", "有可驗證成果")
    model.configure_provider("https://example.invalid/chat", "fixture", 0.5, 1.0, 2.0)
    reopened = StudioUIModel(tmp_path)
    reopened.select_project(project.project_id)
    assert reopened.provider.model == "fixture"
    assert reopened.budget.budget_micro_usd == 500000
    with reopened.service() as studio:
        settings = studio.projects.settings(project.project_id)
        assert "api_key" not in settings
        with pytest.raises(StudioError):
            studio.projects.update_settings(
                project.project_id,
                studio.projects.get(project.project_id).version,
                {"api_key": "never persist"},
            )


def test_multiple_deliverables_have_persistent_case_mapping(tmp_path):
    import copy

    from compiler_helpers import build_ir
    from ir_cases import valid_ir

    payload = valid_ir()
    second = copy.deepcopy(payload["nodes"][0])
    second["id"] = "second"
    second["postconditions"][0]["reference"] = "artifact://second/result"
    payload["nodes"].append(second)
    payload["outputs"] = {"first": "artifact://copy/result", "second": "artifact://second/result"}
    studio, p, _, _ = build_ir(tmp_path, payload)
    studio.close()
    model = StudioUIModel(tmp_path / "studio", runtime_python=runtime_python())
    model.select_project(p.project_id)
    source = tmp_path / "sample.txt"
    source.write_text("multiple deliverables")
    one = model.add_case(source, source, "first")
    model.add_case(source, source, "second")
    assert model.cases()[0]["specification"]["output_name"] == "first"
    assert model.verify()["status"] == "passed"
    model.set_case_output(one["case_id"], "second")
    with pytest.raises(StudioError):
        model.approve_publish(
            tmp_path / "installed", "user", "old report cannot authorize new acceptance"
        )


def test_real_tk_buttons_complete_folder_to_verification(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from workflow_studio.ui.app import StudioApp

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Native Tk display unavailable: {exc}")
    root.withdraw()
    errors = []
    root.report_callback_exception = lambda kind, value, trace: errors.append(str(value))
    monkeypatch.setattr(
        "workflow_studio.ui.app.messagebox.showerror",
        lambda title, message, **kwargs: errors.append(message),
    )
    monkeypatch.setattr(
        socket.socket,
        "connect",
        lambda *args: (_ for _ in ()).throw(AssertionError("UI startup must not use the network")),
    )
    try:
        model = StudioUIModel(tmp_path / "studio", runtime_python=runtime_python())
        app = StudioApp(tmp_path / "studio", root=root, model=model)
        if os.environ.get("STUDIO_HEADLESS_UI_TESTS") == "1":
            root.deiconify()
            root.update()
        app.name_var.set("按鈕測試")
        app.goal_var.set("複製檔案")
        app.success_var.set("內容相符")
        app.new_button.invoke()
        folder = case_folder(tmp_path)
        monkeypatch.setattr(
            "workflow_studio.ui.app.filedialog.askdirectory", lambda **kwargs: str(folder)
        )
        app.import_folder()
        assert app.selected_source
        app.find_folder_candidates()
        deadline = time.monotonic() + 20
        while app.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(0.02)
        assert not app.busy and not errors, errors
        app.accept_candidate()
        assert app.canvas.find_all()
        app.verify_button.invoke()
        deadline = time.monotonic() + 30
        while app.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(0.02)
        assert not app.busy and not errors, errors
        assert "試做通過" in app.report_text.get("1.0", "end")
        target = tmp_path / "installed"
        target.mkdir()
        monkeypatch.setattr(
            "workflow_studio.ui.app.filedialog.askdirectory", lambda **kwargs: str(target)
        )
        app.actor_var.set("UI 測試者")
        app.reviewed.set(True)
        app.refresh_actions()
        publish = next(button for button, rule in app.button_rules.items() if rule == "publish")
        assert not publish.instate(["disabled"])
        publish.invoke()
        deadline = time.monotonic() + 20
        while app.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(0.02)
        assert not app.busy and not errors, errors
        assert list(target.glob("*/flow.json"))
        if os.environ.get("STUDIO_GATE_REPORT_DIR"):
            from test_ui_guidance import capture_window

            capture_window(
                root, Path(os.environ["STUDIO_GATE_REPORT_DIR"]) / "workflow-published.png"
            )
        from PIL import Image

        image_path = tmp_path / "預覽圖.png"
        Image.new("RGB", (400, 200), "blue").save(image_path)
        image_source = model.import_path(image_path)
        app.refresh_project()
        app.source_tree.selection_set(image_source.source_id + ":0")
        app._source_selected()
        width, height = app.display_image.width(), app.display_image.height()
        assert 0 < width <= 400 and 0 < height <= 200
        assert abs(width - 2 * height) <= 1  # Integer pixels may round by one pixel.
        identity = model.project_id
        destroy_test_window(root)
        root = tk.Tk()
        root.withdraw()
        root.report_callback_exception = lambda kind, value, trace: errors.append(str(value))
        reopened = StudioUIModel(tmp_path / "studio", runtime_python=runtime_python())
        app = StudioApp(tmp_path / "studio", root=root, model=reopened)
        app.load_project(identity)
        root.update()
        assert app.current_project.project_id == identity
        assert app.display_ir is not None and app.canvas.find_all()
        assert reopened.release_history() and not errors, errors
    finally:
        try:
            capture_virtual_window(root, "workflow-final.png")
        finally:
            destroy_test_window(root)


def test_tk_analysis_is_visible_before_final_revision(tmp_path, monkeypatch):
    import threading

    from test_discovery import FixtureProvider
    from workflow_studio.llm.usage import Budget

    tk = pytest.importorskip("tkinter")
    from workflow_studio.ui.app import StudioApp

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Native Tk display unavailable: {exc}")
    root.withdraw()
    release = threading.Event()

    class SlowFixture(FixtureProvider):
        def stream(self, messages, maximum):
            for i, chunk in enumerate(super().stream(messages, maximum)):
                yield chunk
                if i == 0:
                    release.wait(5)

    errors = []
    root.report_callback_exception = lambda kind, value, trace: errors.append(str(value))
    monkeypatch.setattr(
        "workflow_studio.ui.app.messagebox.showerror",
        lambda title, message, **kwargs: errors.append(message),
    )
    try:
        model = StudioUIModel(tmp_path, runtime_python=runtime_python())
        model.create_project("串流測試", "完整複製輸入檔案", "輸出內容與輸入相同")
        source = model.import_text("請複製檔案")
        model.provider = SlowFixture()
        model.budget = Budget(3, 1_000_000, 8000, 1_000_000, 1_000_000)
        app = StudioApp(tmp_path, root=root, model=model)
        if os.environ.get("STUDIO_HEADLESS_UI_TESTS") == "1":
            root.deiconify()
            root.update()
        app.load_project(model.project_id)
        model.provider = SlowFixture()
        model.budget = Budget(3, 1_000_000, 8000, 1_000_000, 1_000_000)
        app.source_tree.selection_set(source.source_id)
        app._source_selected()
        app.analyze()
        deadline = time.monotonic() + 4
        while "根據來源" not in app.analysis.get("1.0", "end") and time.monotonic() < deadline:
            root.update()
            time.sleep(0.02)
        assert "根據來源" in app.analysis.get("1.0", "end")
        assert model.revision() is None
        capture_virtual_window(root, "workflow-streaming.png")
        release.set()
        deadline = time.monotonic() + 10
        while app.busy and time.monotonic() < deadline:
            root.update()
            time.sleep(0.02)
        assert not errors, errors
        assert model.revision().status == "ir_ready"
    finally:
        release.set()
        destroy_test_window(root)
