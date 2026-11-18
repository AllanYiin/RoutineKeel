from workflow_studio.ui.wizard import access, primary


def test_no_future_page_without_saved_project_or_verification():
    assert access({}, 0)
    assert all(not access({}, index) for index in range(1, 5))
    state = {"project": True, "goal": "copy", "success": "equal", "workflow": True}
    assert access(state, 1) and access(state, 2) and access(state, 3)
    assert not access(state, 4)
    assert access({**state, "verified": True}, 4)
    for change in (
        {"dirty": True},
        {"conflict": True},
        {"archived": True},
        {"review_blocked": True},
    ):
        assert not access({**state, "verified": True, **change}, 4)


def test_primary_does_not_offer_focus_only_or_disabled_actions():
    assert primary({}, {}, 0)[1] is None
    assert primary({"name": "draft"}, {}, 0)[1] == "create"
    assert primary({"project": True, "name": "draft", "dirty": True}, {}, 0)[1] == "save"
    assert primary({"busy": True}, {"publish": True}, 4)[1] is None
    assert primary({"project": True}, {}, 4)[1] is None
    assert primary({"project": True, "verified": True}, {"publish": True}, 4)[1] == "publish"


def test_empty_folder_guides_to_more_evidence_instead_of_repeating():
    state = {"project": True, "has_sources": True, "selected": True, "folder_needs_more": True}
    assert primary(state, {"folder": True}, 1)[1] == "import_folder"


def test_saved_draft_can_add_sources_before_success_criteria_are_complete():
    state = {"project": True, "name": "draft"}
    assert access(state, 1)
    assert primary(state, {}, 0)[1] == "page:1"
    assert not access(state, 3)
