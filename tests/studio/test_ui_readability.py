import pytest
from workflow_studio.ui.display import enable_high_dpi
from workflow_studio.ui.model import StudioUIModel

pytest.importorskip("PIL")


def test_ui_state_multiline_and_bounds(tmp_path, monkeypatch):
    tk = pytest.importorskip("tkinter")
    from tkinter import font

    from workflow_studio.ui.app import StudioApp

    enable_high_dpi()
    try:
        app = StudioApp(tmp_path)
    except tk.TclError as exc:
        pytest.skip(f"Native Tk display unavailable: {exc}")
    root = app.root
    root.withdraw()
    errors = []
    root.report_callback_exception = lambda kind, value, trace: errors.append(str(value))
    monkeypatch.setattr(
        "workflow_studio.ui.app.messagebox.showerror",
        lambda title, message, **kwargs: errors.append(message),
    )
    try:
        assert (
            font.nametofont("TkDefaultFont", root=root).actual("family")
            == app.display["font_family"]
        )
        assert str(app.verify_button["state"]) == "disabled"
        assert all(
            str(button["state"]) == "disabled"
            for button, rule in app.button_rules.items()
            if rule in {"source", "node", "publish", "active"}
        )
        app.name_var.set("清晰介面測試")
        app.goal_input.insert("1.0", "第一行目標\n第二行目標")
        app.success_input.insert("1.0", "條件一\n條件二")
        root.update()
        assert app.goal_var.get() == "第一行目標\n第二行目標"
        assert app.success_var.get() == "條件一\n條件二"
        app.new_button.invoke()
        assert app.model.project().goal["success_criteria"] == ["條件一", "條件二"]
        assert not app.has_unsaved_changes()
        app.goal_var.set("尚未儲存的內容")
        assert app.has_unsaved_changes()
        assert app.save_state.get() == "尚未儲存"
        assert all(
            str(button["state"]) == "disabled"
            for button, rule in app.button_rules.items()
            if rule == "active"
        )
        monkeypatch.setattr(
            "workflow_studio.ui.app.messagebox.askyesnocancel", lambda *a, **kw: None
        )
        current = app.model.project_id
        app.start_new_project()
        assert app.model.project_id == current
        assert app.goal_var.get() == "尚未儲存的內容"
        monkeypatch.setattr(
            "workflow_studio.ui.app.messagebox.askyesnocancel", lambda *a, **kw: True
        )
        app.start_new_project()
        assert app.model.project_id is None
        with app.model.service() as studio:
            assert studio.projects.get(current).goal["description"] == "尚未儲存的內容"
        assert app.name_var.get() == ""
        root.geometry(f"{round(980 * app.display['scale'])}x{round(640 * app.display['scale'])}")
        app.tabs.select(2)
        root.deiconify()
        root.update()
        root.focus_force()
        app.actor_entry.focus_set()
        root.update()
        limit = root.winfo_rooty() + root.winfo_height()
        assert app.stop_button.winfo_rooty() + app.stop_button.winfo_height() <= limit
        publish = next(button for button, rule in app.button_rules.items() if rule == "publish")
        assert publish.winfo_rooty() + publish.winfo_height() <= limit
        for button in app.buttons:
            if button.winfo_ismapped():
                assert button.winfo_width() >= button.winfo_reqwidth() - 2, button["text"]
        assert not errors
    finally:
        from test_ui_workflow import capture_virtual_window, destroy_test_window

        try:
            capture_virtual_window(root, "workflow-readability-final.png")
        finally:
            destroy_test_window(root)


def test_settings_failure_does_not_change_active_profile(tmp_path):
    from workflow_studio.contracts import StudioError

    model = StudioUIModel(tmp_path)
    model.create_project("設定驗證", "保留原設定", "失敗不變更")
    model.configure_provider("https://first.invalid/chat", "first", 1, 1, 1)
    original = model.provider
    with pytest.raises(StudioError):
        model.configure_provider(
            "https://second.invalid/chat",
            "second",
            1,
            1,
            1,
            runtime_enabled=True,
            runtime_budget_usd=0,
        )
    assert model.provider is original
    assert model.provider.model == "first"
