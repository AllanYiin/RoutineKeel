import os
from types import SimpleNamespace

import pytest
from test_project_edits import Variable
from workflow_studio.ui.app import StudioApp
from workflow_studio.ui.guidance import blocked_reason, next_step


def ready(**changes):
    return {"project": True, "name": "test", "goal": "copy", "success": "equal", **changes}


def test_inherited_empty_label_wrap_does_not_break_window_construction(monkeypatch):
    from workflow_studio.ui.layout import fit_wrapped_labels

    class Label:
        def __init__(self, wrap):
            self.wrap, self.bound = wrap, None

        def cget(self, name):
            return self.wrap

        def winfo_pixels(self, value):
            assert value != "", "Tk rejects an empty screen distance"
            return int(value)

        def winfo_children(self):
            return []

        def bind(self, event, callback, **kwargs):
            self.bound = callback

        def configure(self, **kwargs):
            self.wrap = kwargs["wraplength"]

    plain, wrapping = Label(""), Label(270)
    monkeypatch.setattr("workflow_studio.ui.layout.ttk.Label", Label)
    fit_wrapped_labels(SimpleNamespace(winfo_children=lambda: [plain, wrapping]), 80)
    assert plain.bound is None
    wrapping.bound(SimpleNamespace(width=180))
    assert wrapping.wrap == 180


@pytest.mark.parametrize(
    "state, action",
    [
        ({}, "focus_name"),
        ({"name": "test"}, "focus_goal"),
        ({"name": "test", "goal": "copy"}, "focus_success"),
        ({"name": "test", "goal": "copy", "success": "equal"}, "create_project"),
        (ready(), "paste_source"),
        (ready(has_sources=True), "focus_sources"),
        (ready(has_sources=True, selected=True, single_folder=True), "find_folder_candidates"),
        (ready(has_sources=True, selected=True), "configure_model"),
        (ready(has_sources=True, selected=True, provider=True), "analyze"),
        (ready(has_sources=True, candidates=True), "focus_candidate"),
        (ready(has_sources=True, candidates=True, candidate_selected=True), "accept_candidate"),
        (ready(has_sources=True, workflow=True), "add_case"),
        (ready(has_sources=True, workflow=True, cases=1), "verify"),
        (ready(has_sources=True, workflow=True, cases=1, verified=True), "focus_review"),
        (
            ready(has_sources=True, workflow=True, cases=1, verified=True, reviewed=True),
            "focus_actor",
        ),
        (
            ready(
                has_sources=True,
                workflow=True,
                cases=1,
                verified=True,
                reviewed=True,
                actor="reviewer",
            ),
            "publish",
        ),
        (
            ready(has_sources=True, workflow=True, cases=1, verified=True, published=True),
            "release_history",
        ),
        (ready(dirty=True), "save_project"),
        (ready(dirty=True, goal=""), "focus_goal"),
        (ready(dirty=True, conflict=True), "compare_project_updates"),
        (ready(archived=True), "restore_project"),
        (ready(busy=True), None),
    ],
)
def test_each_stage_has_one_explicit_next_action(state, action):
    result = next_step(state)
    assert result.action == action
    assert result.title and result.detail and result.label


def test_failure_and_review_hold_never_offer_publication():
    state = ready(has_sources=True, workflow=True, cases=1, reviewed=True, actor="reviewer")
    assert next_step({**state, "report_failed": True}).action == "show_results"
    assert next_step({**state, "report_failed": True, "repairable": True}).action == "repair"
    assert next_step({**state, "verified": True, "review_blocked": True}).action == "manage_builds"
    assert (
        next_step({**state, "candidates": True, "candidate_selected": True}).action
        == "accept_candidate"
    )
    assert "試做" in blocked_reason("publish", state)
    assert "模型設定" in blocked_reason("analysis", ready(selected=True))
    assert "金鑰" in blocked_reason("analysis", ready(selected=True, provider_configured=True))
    assert "一個來源" in blocked_reason("single_source", ready(selected=True, selected_count=2))
    assert (
        next_step(
            ready(has_sources=True, selected=True, single_folder=True, folder_needs_more=True)
        ).action
        == "import_folder"
    )


def test_disabled_click_explains_without_running_the_action():
    app = StudioApp.__new__(StudioApp)
    widget = object()
    app.guidance_feedback = Variable()
    app.refresh_actions = lambda: None
    app.button_blockers = {widget: "請先設定模型；尚未傳送任何資料。"}
    calls = []
    app.guard = lambda action: action()
    app.invoke_button(widget, lambda: calls.append("sent"))
    assert calls == [] and "設定模型" in app.guidance_feedback.get()
    assert app.explain_blocked_button(widget) == "break"
    app.button_blockers.clear()
    app.invoke_button(widget, lambda: calls.append("allowed"))
    assert calls == ["allowed"]


def test_saved_model_without_session_key_is_not_presented_as_ready(tmp_path, monkeypatch):
    from workflow_studio.ui.model import StudioUIModel

    model = StudioUIModel(tmp_path)
    model.configure_provider("https://example.invalid/chat", "fixture", 0.5, 1.0, 2.0)
    monkeypatch.delenv(model.provider.api_key_env, raising=False)
    assert not model.provider_ready
    guidance = next_step(ready(has_sources=True, selected=True, provider_configured=True))
    assert guidance.action == "configure_model" and "金鑰" in guidance.title
    monkeypatch.setenv(model.provider.api_key_env, "private-fixture-do-not-display")
    assert model.provider_ready
    assert "private-fixture" not in str(model.session_config)


def test_imported_file_becomes_the_selected_preview(monkeypatch):
    app = StudioApp.__new__(StudioApp)
    selected, focused, previews = [], [], []
    app.root = object()
    app.model = SimpleNamespace(import_path=lambda path: SimpleNamespace(source_id="new-source"))
    app.refresh_project = lambda: None
    app.source_tree = SimpleNamespace(
        selection_set=lambda identity: selected.append(identity),
        focus=lambda identity: focused.append(identity),
        see=lambda identity: None,
    )
    app._source_selected = lambda: previews.append((selected[-1], focused[-1]))
    monkeypatch.setattr(
        "workflow_studio.ui.app.filedialog.askopenfilename", lambda **kwargs: "example.txt"
    )
    app.import_file()
    assert previews == [("new-source", "new-source")]


def test_folder_actions_use_current_selection_and_freeze_the_requested_owner(monkeypatch):
    app = StudioApp.__new__(StudioApp)
    old = SimpleNamespace(source_id="old", version=1, kind="text")
    folder = SimpleNamespace(source_id="folder", version=3, kind="folder")
    app.source_rows = {"old": (old, None), "folder:child": (folder, {})}
    selected = ["folder:child"]
    app.source_tree = SimpleNamespace(selection=lambda: tuple(selected))
    app.selected_source = "old"
    app.root = object()
    app.status = Variable()
    calls, pending = [], []
    app.model = SimpleNamespace(
        import_path=lambda path, identity: calls.append((path, identity)),
        folder_candidates=lambda identity: calls.append(identity),
    )
    app.refresh_project = lambda: None
    app.focus_imported_source = lambda identity: None
    monkeypatch.setattr("workflow_studio.ui.app.filedialog.askdirectory", lambda **kw: "new-folder")
    monkeypatch.setattr(
        "workflow_studio.ui.app.filedialog.askopenfilename",
        lambda **kw: pytest.fail("folder replacement must not open a file chooser"),
    )
    app.replace_source()
    assert calls == [("new-folder", "folder")]
    app._background = lambda action, done: pending.append(action)
    app.find_folder_candidates()
    selected[:] = ["old"]
    folder.source_id = "changed-after-request"
    pending[0]()
    assert calls[-1] == "folder"


@pytest.mark.skipif(
    os.environ.get("STUDIO_HEADLESS_UI_TESTS") != "1",
    reason="Explicit isolated virtual-display UI gate only",
)
def test_headless_guidance_disabled_feedback_and_progressive_disclosure(tmp_path, monkeypatch):
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    errors = []
    root.report_callback_exception = lambda kind, value, traceback: errors.append(
        f"{kind.__name__}: {value}"
    )
    app = StudioApp(tmp_path / "workspace", root=root)
    app.error = lambda exc: errors.append(repr(exc))
    try:
        root.deiconify()
        root.update()
        root.focus_force()
        assert not errors, errors
        assert app.next_button.winfo_ismapped()
        tools = next(b for b in app.buttons if b["text"] == "工具管理")
        assert not tools.winfo_ismapped()
        file_button = next(b for b in app.buttons if b["text"] == "檔案")
        assert file_button.instate(["disabled"])
        file_button.event_generate("<Button-1>", x=4, y=4)
        root.update()
        assert "建立專案" in app.guidance_feedback.get()
        assert app.model.project_id is None
        app.name_var.set("引導測試")
        app.goal_var.set("完整複製文字")
        app.success_var.set("內容相同")
        root.update()
        assert app.current_guidance.action == "create_project"
        app.next_button.invoke()
        root.update()
        assert not errors, errors
        assert app.current_guidance.action == "paste_source"
        sample = tmp_path / "sample.txt"
        sample.write_text("synthetic preview", encoding="utf-8")
        monkeypatch.setattr(
            "workflow_studio.ui.app.filedialog.askopenfilename", lambda **kwargs: str(sample)
        )
        file_button.invoke()
        root.update()
        assert not errors, errors
        assert len(app.source_tree.selection()) == 1
        assert "synthetic preview" in app.preview_text.get("1.0", "end")
        assert app.current_guidance.action == "configure_model"
        app.advanced_var.set(True)
        app.refresh_actions()
        root.update()
        assert tools.winfo_ismapped()
        app.advanced_var.set(False)
        app.refresh_actions()
        root.update()
        assert not tools.winfo_ismapped()
        app.goal_input.focus_set()
        root.update()
        original = app.goal_var.get()
        app.goal_input.event_generate("<Tab>")
        root.update()
        assert app.goal_var.get() == original
        assert root.focus_get() != app.goal_input
        ai_button = next(b for b in app.buttons if b["text"] == "AI 整理選取來源")
        assert ai_button.instate(["disabled"])
        ai_button.event_generate("<Button-1>", x=4, y=4)
        root.update()
        assert "模型設定" in app.guidance_feedback.get()
        assert app.model.provider is None
        if os.environ.get("STUDIO_GATE_REPORT_DIR"):
            from pathlib import Path

            from PIL import ImageGrab

            output = Path(os.environ["STUDIO_GATE_REPORT_DIR"])
            output.mkdir(parents=True, exist_ok=True)
            ImageGrab.grab().save(output / "guided-workbench.png")
        root.geometry(f"{round(980 * app.display['scale'])}x{round(640 * app.display['scale'])}")
        app.advanced_var.set(True)
        app.refresh_actions()
        app.tabs.select(2)
        root.update()
        app.actor_entry.focus_set()
        root.update()
        assert (
            app.next_button.winfo_rooty() + app.next_button.winfo_height()
            <= root.winfo_rooty() + root.winfo_height()
        )
        column = app.columns[2]
        assert app.actor_entry.winfo_rooty() >= column.canvas.winfo_rooty() - 2
        assert (
            app.actor_entry.winfo_rooty() + app.actor_entry.winfo_height()
            <= column.canvas.winfo_rooty() + column.canvas.winfo_height() + 2
        )
        for column in app.columns:
            assert column.body.winfo_width() <= column.canvas.winfo_width() + 1
        assert not errors, errors
        if os.environ.get("STUDIO_GATE_REPORT_DIR"):
            ImageGrab.grab().save(output / "guided-workbench-compact.png")
    finally:
        if os.environ.get("STUDIO_GATE_REPORT_DIR"):
            try:
                from pathlib import Path

                from PIL import ImageGrab

                output = Path(os.environ["STUDIO_GATE_REPORT_DIR"])
                output.mkdir(parents=True, exist_ok=True)
                root.update_idletasks()
                ImageGrab.grab().save(output / "guided-workbench-final.png")
            except Exception as exc:
                (output / "guided-screenshot-error.txt").write_text(str(exc), encoding="utf-8")
        root.destroy()
