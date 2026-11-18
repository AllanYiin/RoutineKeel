"""Exercise the real workbench refresh/save controller with no native window."""

from types import SimpleNamespace

import pytest
from workflow_studio.contracts import StudioError
from workflow_studio.ui.app import StudioApp
from workflow_studio.ui.model import StudioUIModel
from workflow_studio.ui.project_edits import ProjectEdits


class Variable:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def test_partial_goal_can_be_saved_reopened_and_completed(tmp_path):
    model = StudioUIModel(tmp_path)
    draft = model.create_project("Draft", "", "")
    reopened = StudioUIModel(tmp_path)
    reopened.select_project(draft.project_id)
    assert reopened.project().goal == {"description": "", "success_criteria": []}
    partial = reopened.update_project("Draft", "copy text", "", draft.version)
    reopened = StudioUIModel(tmp_path)
    reopened.select_project(draft.project_id)
    assert reopened.project().goal == {"description": "copy text", "success_criteria": []}
    source = reopened.import_text("synthetic source")
    assert source.source_id
    complete = reopened.update_project(
        "Draft", "copy text", "identical output", reopened.project().version
    )
    assert complete.version > partial.version
    assert complete.goal["success_criteria"] == ["identical output"]


class ListView:
    def __init__(self):
        self.rows, self.selected = {}, []

    def get_children(self):
        return list(self.rows)

    def delete(self, *items):
        for item in items:
            self.rows.pop(item, None)

    def insert(self, parent, position, *, iid, **kwargs):
        self.rows[iid] = kwargs

    def selection_clear(self, *args):
        self.selected = []

    def selection(self):
        return tuple(self.selected)

    def selection_set(self, item):
        self.selected = list(item) if isinstance(item, (list, tuple)) else [item]


def controller(tmp_path):
    model = StudioUIModel(tmp_path)
    project = model.create_project("Project", "original goal", "original success")
    app = StudioApp.__new__(StudioApp)
    app.model = model
    app.project_edits = ProjectEdits()
    app.current_project = app.selected_source = None
    app.name_var, app.goal_var, app.success_var = Variable(), Variable(), Variable()
    app.case_label, app.reviewed, app.status = Variable(), Variable(False), Variable()
    app.source_tree, app.project_list = ListView(), ListView()
    app.project_rows = [project]
    app.draw_workflow = lambda **kwargs: None
    app.refresh_actions = lambda: None
    app.refresh_projects = lambda: None
    app.root = SimpleNamespace()
    app.close_requested = False
    app.error = lambda exc: None
    app.refresh_project()
    return app, project


@pytest.mark.parametrize("save", [None, True])
def test_background_close_respects_unsaved_draft_decision(tmp_path, monkeypatch, save):
    import queue

    app, project = controller(tmp_path)
    app.goal_var.set("unsaved goal while background work finishes")
    app.busy = app.close_requested = True
    app.messages = queue.Queue()
    started, closed, scheduled = [], [], []
    app.messages.put(("done", lambda _value: started.append("next phase"), None))
    app.stop_button = SimpleNamespace(configure=lambda **kw: None)
    app.root = SimpleNamespace(
        destroy=lambda: closed.append(True), after=lambda *args: scheduled.append(True)
    )
    monkeypatch.setattr("workflow_studio.ui.app.messagebox.askyesnocancel", lambda *a, **kw: save)
    app._drain()
    assert started == []
    assert bool(closed) == (save is True)
    with app.model.service() as studio:
        saved_goal = studio.projects.get(project.project_id).goal["description"]
    if save:
        assert saved_goal == "unsaved goal while background work finishes"
    else:
        assert saved_goal == "original goal"
        assert app.goal_var.get() == "unsaved goal while background work finishes"
        assert not app.close_requested and scheduled


def background_source(app):
    with app.model.service() as studio:
        p = studio.projects.get(app.model.project_id)
        return studio.sources.import_text(
            p.project_id, p.version, "background source", name="evidence.txt"
        )


def external_goal(app, description="external goal"):
    with app.model.service() as studio:
        p = studio.projects.get(app.model.project_id)
        return studio.projects.update(
            p.project_id, p.version, goal={**p.goal, "description": description}
        )


def test_background_refresh_preserves_verbatim_multiline_draft_and_saves_it(tmp_path):
    app, p = controller(tmp_path)
    app.goal_var.set("local first line\n\n  local last line  ")
    app.success_var.set(" new criterion one \n\nnew criterion two")
    before = (app.goal_var.get(), app.success_var.get())
    source = background_source(app)
    app.refresh_project()
    assert (app.goal_var.get(), app.success_var.get()) == before
    assert app.has_unsaved_changes() and not app.project_edits.conflict
    assert source.source_id in app.source_tree.rows
    assert app.project_edits.base.version == app.model.project().version
    app.save_project()
    saved = app.model.project()
    assert saved.project_id == p.project_id and saved.goal["description"] == before[0]
    assert saved.goal["success_criteria"] == ["new criterion one", "new criterion two"]
    assert not app.has_unsaved_changes()


def test_external_goal_does_not_rebase_conflicting_draft(tmp_path):
    app, original = controller(tmp_path)
    app.goal_var.set("local draft")
    external = external_goal(app)
    for _ in range(3):
        app.refresh_project()
        assert app.current_project == external
        assert app.project_edits.base == original
        assert app.goal_var.get() == "local draft" and app.project_edits.conflict
    with pytest.raises(StudioError) as exc:
        app.save_project()
    assert exc.value.code == "REVISION_CONFLICT"
    assert app.model.project().goal["description"] == "external goal"


def test_comparison_choice_is_bound_to_the_reviewed_version(tmp_path):
    app, _ = controller(tmp_path)
    app.goal_var.set("chosen local draft")
    observed = external_goal(app)
    app.refresh_project()
    values = (app.name_var.get(), app.goal_var.get(), app.success_var.get())
    newer = external_goal(app, "even newer goal")
    with pytest.raises(StudioError):
        app.save_project_values(values, observed)
    assert app.model.project() == newer
    assert app.goal_var.get() == "chosen local draft"
    # A fresh, explicit comparison choice can save against its exact observed snapshot.
    app.save_project_values(values, newer)
    assert app.model.project().goal["description"] == "chosen local draft"
    assert not app.has_unsaved_changes()


def test_unsaved_fields_are_kept_when_save_discovers_a_conflict(tmp_path):
    app, _ = controller(tmp_path)
    app.name_var.set("local name")
    external = external_goal(app)
    with pytest.raises(StudioError):
        app.save_project()
    assert app.name_var.get() == "local name"
    assert app.project_edits.conflict and app.current_project == external


def test_explicit_discard_loads_latest_but_same_project_selection_keeps_draft(tmp_path):
    app, p = controller(tmp_path)
    app.goal_var.set("local draft")
    app.load_project(p.project_id)
    assert app.goal_var.get() == "local draft"
    external_goal(app)
    app.refresh_project()
    assert app.project_edits.conflict
    app.refresh_project(discard_edits=True)
    assert app.goal_var.get() == "external goal"
    assert not app.has_unsaved_changes() and not app.project_edits.conflict


def test_clean_fields_follow_external_change_without_a_false_conflict(tmp_path):
    app, _ = controller(tmp_path)
    latest = external_goal(app)
    app.refresh_project()
    assert app.goal_var.get() == "external goal"
    assert app.project_edits.base == latest and not app.has_unsaved_changes()


def test_cancel_switch_and_save_failure_keep_original_project_and_draft(tmp_path, monkeypatch):
    app, p = controller(tmp_path)
    with app.model.service() as studio:
        other = studio.projects.create("other")
    app.project_rows.append(other)
    app.goal_var.set("must keep")
    monkeypatch.setattr(
        "workflow_studio.ui.app.messagebox.askyesnocancel", lambda *args, **kwargs: None
    )
    app.load_project(other.project_id)
    assert app.model.project_id == p.project_id and app.goal_var.get() == "must keep"
    assert app.project_list.selected == [0]
    external_goal(app)
    monkeypatch.setattr(
        "workflow_studio.ui.app.messagebox.askyesnocancel", lambda *args, **kwargs: True
    )
    app.load_project(other.project_id)
    assert app.model.project_id == p.project_id and app.goal_var.get() == "must keep"


def test_switch_without_save_and_stale_dialog_cannot_write_other_project(tmp_path, monkeypatch):
    app, p = controller(tmp_path)
    with app.model.service() as studio:
        other = studio.projects.create(
            "other", {"description": "other goal", "success_criteria": ["other result"]}
        )
    app.goal_var.set("discarded local text")
    monkeypatch.setattr(
        "workflow_studio.ui.app.messagebox.askyesnocancel", lambda *args, **kwargs: False
    )
    app.load_project(other.project_id)
    assert app.model.project_id == other.project_id and app.goal_var.get() == "other goal"
    with pytest.raises(StudioError):
        app.save_project_values(("wrong", "wrong", "wrong"), p)
    assert app.model.project() == other


def test_unsaved_new_project_also_requires_leave_decision(tmp_path, monkeypatch):
    app, p = controller(tmp_path)
    app.current_project = None
    app.project_edits.accept(None)
    app.model.project_id = None
    app.name_var.set("not yet created")
    monkeypatch.setattr(
        "workflow_studio.ui.app.messagebox.askyesnocancel", lambda *args, **kwargs: None
    )
    app.load_project(p.project_id)
    assert app.model.project_id is None and app.name_var.get() == "not yet created"


def test_nonvisible_goal_metadata_survives_ui_update(tmp_path):
    app, _ = controller(tmp_path)
    with app.model.service() as studio:
        p = app.model.project()
        studio.projects.update(
            p.project_id, p.version, goal={**p.goal, "retained_metadata": {"owner": "local"}}
        )
    app.goal_var.set("new visible description")
    app.refresh_project()
    app.save_project()
    assert app.model.project().goal["retained_metadata"] == {"owner": "local"}


def test_archival_keeps_draft_and_does_not_acquire_new_save_authority(tmp_path):
    app, original = controller(tmp_path)
    app.goal_var.set("keep during archive")
    with app.model.service() as studio:
        archived = studio.projects.archive(original.project_id, original.version)
    app.refresh_project()
    assert app.current_project == archived and app.project_edits.base == original
    assert app.project_edits.conflict and app.goal_var.get() == "keep during archive"
    with pytest.raises(StudioError):
        app.save_project_values(("Project", "keep during archive", "criterion"), archived)
    assert app.model.project() == archived


def test_editing_during_preparation_prevents_analysis_of_an_outdated_goal(tmp_path, monkeypatch):
    app, project = controller(tmp_path)
    app.goal_var.set("new goal typed while preparing")
    prepared = SimpleNamespace(project_id=project.project_id)
    monkeypatch.setattr(
        "workflow_studio.ui.analysis_disclosure.open_disclosure",
        lambda *args: pytest.fail("must not offer a request for an unsaved goal"),
    )
    app.offer_analysis(prepared)
    assert "沒有傳送" in app.status.get()
    with pytest.raises(StudioError):
        app.start_analysis(prepared)
    assert app.goal_var.get() == "new goal typed while preparing"


def test_comparison_with_identical_external_text_can_converge_without_overwrite(tmp_path):
    app, _ = controller(tmp_path)
    app.goal_var.set("same result")
    latest = external_goal(app, "same result")
    app.refresh_project()
    assert app.project_edits.base == latest and not app.has_unsaved_changes()
    assert app.goal_var.get() == "same result"


def test_external_change_after_preparation_requires_a_new_request(tmp_path, monkeypatch):
    app, project = controller(tmp_path)
    prepared = SimpleNamespace(project_id=project.project_id, project_version=project.version)
    external_goal(app)
    monkeypatch.setattr(
        "workflow_studio.ui.analysis_disclosure.open_disclosure",
        lambda *args: pytest.fail("must not disclose a stale prepared request"),
    )
    app.offer_analysis(prepared)
    assert "沒有傳送" in app.status.get()
    assert app.goal_var.get() == "external goal"


def test_unchanged_legacy_criteria_do_not_become_dirty_or_get_rewritten(tmp_path):
    app, _ = controller(tmp_path)
    original = [" padded criterion ", "one criterion\nwith two lines"]
    with app.model.service() as studio:
        p = app.model.project()
        studio.projects.update(
            p.project_id, p.version, goal={**p.goal, "success_criteria": original}
        )
    app.refresh_project()
    assert not app.has_unsaved_changes()
    app.name_var.set("renamed only")
    app.save_project()
    assert app.model.project().goal["success_criteria"] == original
