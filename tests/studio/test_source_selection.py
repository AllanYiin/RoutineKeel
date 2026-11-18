from types import SimpleNamespace

import pytest
from test_project_edits import controller
from workflow_studio.contracts import StudioError
from workflow_studio.ui.app import StudioApp


def test_analysis_uses_all_selected_source_owners_and_freezes_scope(tmp_path):
    app, _ = controller(tmp_path)
    owners = [SimpleNamespace(source_id="source_a"), SimpleNamespace(source_id="source_b")]
    app.source_rows = {"a": (owners[0], None), "a:0": (owners[0], {}), "b": (owners[1], None)}
    selection = ["a:0", "b", "a"]
    app.source_tree = SimpleNamespace(selection=lambda: tuple(selection))
    app.selected_source = "source_a"
    pending, calls = [], []
    app._background = lambda action, done: pending.append(action)
    app.model.prepare_analysis = lambda ids: calls.append(ids)
    app.analyze()
    selection[:] = ["b"]
    pending[0]()
    assert calls == [["source_a", "source_b"]]


def test_empty_or_stale_selection_never_uses_last_previewed_source(tmp_path):
    app, _ = controller(tmp_path)
    app.selected_source = "source_a"
    app.source_rows = {"a": (SimpleNamespace(source_id="source_a"), None)}
    app._background = lambda *args: pytest.fail("must not prepare an unintended scope")
    for selected in ((), ("missing",)):
        app.source_tree = SimpleNamespace(selection=lambda selected=selected: selected)
        with pytest.raises(StudioError):
            app.analyze()


def test_refresh_preserves_multiple_sources_and_file_identity_after_reordering(tmp_path):
    app, _ = controller(tmp_path / "workspace")
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "a.txt").write_text("a")
    (folder / "z.txt").write_text("z")
    other = tmp_path / "other.txt"
    other.write_text("other")
    first = app.model.import_path(folder)
    second = app.model.import_path(other)
    app.refresh_project()
    app.source_tree.selection_set([first.source_id + ":1", second.source_id])
    app.selected_source = first.source_id
    (folder / "0.txt").write_text("new earlier file")
    app.model.import_path(folder, source_id=first.source_id)
    app.refresh_project()
    selected = [app.source_rows[key] for key in app.source_tree.selection()]
    assert {(s.source_id, record["path"] if record else None) for s, record in selected} == {
        (first.source_id, "z.txt"),
        (second.source_id, None),
    }


def test_deselecting_every_source_clears_the_preview_selection(tmp_path):
    app, _ = controller(tmp_path)
    app.busy = False
    app.selected_source = "old_source"
    app.source_tree.selection_clear()
    app._source_selected()
    assert app.selected_source is None


def test_single_file_parent_previews_content_instead_of_only_its_filename(tmp_path):
    app, _ = controller(tmp_path)
    source = app.model.import_text("actual imported content", "note.txt")
    app.refresh_project()
    app.busy = False
    app.source_tree = SimpleNamespace(
        selection=lambda: (source.source_id,), focus=lambda: source.source_id
    )
    app.image_label = app.preview_label = SimpleNamespace(configure=lambda **kwargs: None)
    app.preview_text = object()
    app.tabs = SimpleNamespace(select=lambda index: None)
    seen = []
    app._set_text = lambda widget, text: seen.append(text)
    app._source_selected()
    assert seen == ["actual imported content"]


def test_multiselect_enables_analysis_but_blocks_single_source_operations(tmp_path):
    app, _ = controller(tmp_path)

    class Widget:
        def configure(self, **kwargs):
            self.settings = kwargs

        def current(self):
            return -1

    analysis, single = Widget(), Widget()
    app.button_rules = {analysis: "analysis", single: "single_source"}
    app.candidate_choice = Widget()
    app.busy = False
    app.display_ir = app.last_report = app.selected_node = None
    app.build_review_blocked = False
    app.model.provider = object()
    owners = [SimpleNamespace(source_id="a"), SimpleNamespace(source_id="b")]
    app.source_rows = {"a": (owners[0], None), "a:0": (owners[0], {}), "b": (owners[1], None)}
    app.source_tree.selection_set(["a", "b"])
    StudioApp.refresh_actions(app)
    assert analysis.settings["state"] == "normal" and single.settings["state"] == "disabled"
    app.source_tree.selection_set(["a", "a:0"])
    StudioApp.refresh_actions(app)
    assert single.settings["state"] == "normal"
    app.source_tree.selection_clear()
    StudioApp.refresh_actions(app)
    assert analysis.settings["state"] == single.settings["state"] == "disabled"


def test_multiselect_previews_focused_file_not_always_first_selection(tmp_path):
    app, _ = controller(tmp_path)
    app.busy = False
    app.source_tree = SimpleNamespace(selection=lambda: ("a", "b"), focus=lambda: "b")
    app.source_rows = {
        key: (
            SimpleNamespace(source_id=key, name=key, version=1, manifest={}),
            {"preview": {"kind": "text", "text": key + " content", "truncated": False}},
        )
        for key in ("a", "b")
    }
    app.image_label = app.preview_label = SimpleNamespace(configure=lambda **kwargs: None)
    app.preview_text = object()
    app.tabs = SimpleNamespace(select=lambda index: None)
    seen = []
    app._set_text = lambda widget, text: seen.append(text)
    app._source_selected()
    assert app.selected_source == "b"
    assert seen == ["b content"]
