import os
from types import SimpleNamespace

import pytest
from test_project_edits import Variable
from workflow_studio.ui.app import StudioApp
from workflow_studio.ui.guidance import blocked_reason, next_step


def ready(**changes):
    return {"project": True, "name": "test", "goal": "copy", "success": "equal", **changes}


def capture_window(root, path):
    from PIL import ImageGrab

    root.update_idletasks()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(path)


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
        ({"name": "test"}, "create_project"),
        ({"name": "test", "goal": "copy"}, "create_project"),
        ({"name": "test", "goal": "copy", "success": "equal"}, "create_project"),
        (ready(), "paste_source"),
        (ready(goal=""), "focus_goal"),
        (ready(success=""), "focus_success"),
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
        (ready(dirty=True, goal=""), "save_project"),
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
    from pathlib import Path

    from test_ui_workflow import destroy_test_window

    root = tk.Tk()
    root.withdraw()
    errors = []
    root.report_callback_exception = lambda kind, value, traceback: errors.append(str(value))
    app = StudioApp(tmp_path / "workspace", root=root)
    app.error = lambda exc: errors.append(repr(exc))
    output = (
        Path(os.environ["STUDIO_GATE_REPORT_DIR"])
        if os.environ.get("STUDIO_GATE_REPORT_DIR")
        else None
    )

    def capture(name):
        if output:
            output.mkdir(parents=True, exist_ok=True)
            capture_window(root, output / name)

    def check_current_page(index):
        root.update()
        assert app.wizard_step == index
        assert [i for i, page in enumerate(app.wizard_pages) if page.winfo_ismapped()] == [index]
        assert all(not b.instate(["disabled"]) for b in app.buttons if b.winfo_ismapped())
        assert not app.project_list.winfo_ismapped()
        assert not errors, errors

    try:
        root.deiconify()
        root.update()
        root.focus_force()
        app.name_entry.focus_set()
        check_current_page(0)
        assert app.name_entry.winfo_ismapped() and not app.name_entry.instate(["disabled"])
        assert app.name_entry.winfo_rootx() < root.winfo_rootx() + root.winfo_width() * 0.2
        assert not app.source_tree.winfo_ismapped()
        assert not app.actor_entry.winfo_ismapped()
        assert not app.canvas.winfo_ismapped()
        assert not app.next_button.winfo_ismapped()
        capture("wizard-start.png")
        # Typing directly works; no separate focus/unlock action is required.
        app.name_entry.insert(0, "引導測試")
        root.update()
        assert app.name_var.get() == "引導測試"
        assert app.next_button.winfo_ismapped()
        next_x = app.next_button.winfo_rootx()
        app.next_button.invoke()
        check_current_page(1)
        assert app.current_project.goal["description"] == ""
        assert not app.name_entry.winfo_ismapped()
        app.back_button.invoke()
        check_current_page(0)
        assert app.name_var.get() == "引導測試"
        app.goal_var.set("完整複製文字")
        app.success_var.set("內容相同")
        root.update()
        assert app.next_button.winfo_rootx() == next_x
        app.next_button.invoke()
        check_current_page(1)
        file_button = next(b for b in app.buttons if b["text"] == "檔案")
        assert file_button.winfo_ismapped()
        sample = tmp_path / "sample.txt"
        sample.write_text("synthetic preview", encoding="utf-8")
        monkeypatch.setattr(
            "workflow_studio.ui.app.filedialog.askopenfilename", lambda **kwargs: str(sample)
        )
        file_button.invoke()
        check_current_page(1)
        assert len(app.source_tree.selection()) == 1
        assert "synthetic preview" in app.preview_text.get("1.0", "end")
        assert app.wizard_action == "configure_model"
        assert app.next_button.winfo_rootx() == next_x
        assert not app.show_wizard_page(4)
        check_current_page(1)
        assert not app.actor_entry.winfo_ismapped()
        capture("guided-workbench.png")

        app.advanced_var.set(True)
        app.refresh_actions()
        root.update()
        description = app.edit_source_description()
        root.update()
        editor = next(w for w in description.winfo_children() if isinstance(w, tk.Text))
        editor.delete("1.0", "end")
        editor.insert("1.0", "合成來源：用來檢查預覽與流程引導。")
        actions = next(w for w in description.winfo_children() if w.winfo_class() == "TFrame")
        next(w for w in actions.winfo_children() if w["text"] == "儲存說明").invoke()
        root.update()
        assert not description.winfo_exists()
        assert "合成來源" in app.preview_label["text"]
        source = app.single_source()
        stale = app.edit_source_description()
        root.update()
        stale_editor = next(w for w in stale.winfo_children() if isinstance(w, tk.Text))
        stale_editor.delete("1.0", "end")
        stale_editor.insert("1.0", "保留這份未儲存草稿")
        with app.model.service() as studio:
            latest = studio.projects.get(app.model.project_id)
            studio.sources.describe(
                latest.project_id, source.source_id, latest.version, "其他編輯者的來源說明"
            )
        stale_actions = next(w for w in stale.winfo_children() if w.winfo_class() == "TFrame")
        conflicts = []
        app.error = conflicts.append
        next(w for w in stale_actions.winfo_children() if w["text"] == "儲存說明").invoke()
        root.update()
        assert conflicts and conflicts[0].code == "REVISION_CONFLICT"
        assert stale.winfo_exists() and stale_editor.get("1.0", "end-1c") == "保留這份未儲存草稿"
        next(w for w in stale_actions.winfo_children() if w["text"] == "取消").invoke()
        app.error = lambda exc: errors.append(repr(exc))
        app.refresh_project()
        app.advanced_var.set(False)
        app.refresh_actions()
        app.back_button.invoke()
        check_current_page(0)
        # Deferred Treeview selection events must not pull the user back to Sources.
        app.refresh_project()
        check_current_page(0)
        root.focus_force()
        app.goal_input.focus_set()
        root.update()
        original = app.goal_var.get()
        app.goal_input.event_generate("<Tab>")
        root.update()
        assert app.goal_var.get() == original
        assert root.focus_get() is not None and root.focus_get().winfo_ismapped()
        root.geometry(f"{round(980 * app.display['scale'])}x{round(640 * app.display['scale'])}")
        root.update()
        assert app.next_button.winfo_rootx() == next_x
        assert (
            app.next_button.winfo_rooty() + app.next_button.winfo_height()
            <= root.winfo_rooty() + root.winfo_height()
        )
        assert app.name_entry.winfo_ismapped()
        for column in app.columns:
            if column.winfo_ismapped():
                assert column.body.winfo_width() <= column.canvas.winfo_width() + 1
        capture("guided-workbench-compact.png")
        check_current_page(0)
    finally:
        capture("guided-workbench-final.png")
        destroy_test_window(root)
