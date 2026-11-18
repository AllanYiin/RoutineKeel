from types import SimpleNamespace

import pytest
from workflow_studio.contracts import StudioError
from workflow_studio.ui.model import StudioUIModel
from workflow_studio.ui.source_description import source_caption


def setup(tmp_path):
    model = StudioUIModel(tmp_path)
    project = model.create_project("來源說明", "完整複製", "內容相同")
    source = model.import_text("unchanged original bytes", "input.txt")
    return model, project, source


def test_description_versions_reopen_without_changing_source_bytes(tmp_path):
    model, project, source = setup(tmp_path)
    model.build_id, model.approval_id, model.candidates = "old-build", "old-approval", ["old"]
    saved = model.describe_source(source.source_id, "用途：人工確認的輸入範例", model.project())
    assert saved.version == source.version + 1
    assert saved.content_hash == source.content_hash
    assert model.build_id is model.approval_id is None and model.candidates == []
    reopened = StudioUIModel(tmp_path)
    reopened.select_project(project.project_id)
    with reopened.service() as studio:
        old = studio.sources.get(project.project_id, source.source_id, version=source.version)
        current = studio.sources.get(project.project_id, source.source_id)
        assert old.manifest.get("description", "") == ""
        assert current.manifest["description"] == "用途：人工確認的輸入範例"
        assert (
            studio.sources.content(project.project_id, source.source_id, "input.txt")
            == b"unchanged original bytes"
        )


def test_stale_description_does_not_overwrite_newer_edit_or_clear_session_state(tmp_path):
    model, _, source = setup(tmp_path)
    expected = model.project()
    model.describe_source(source.source_id, "newer description", expected)
    model.build_id, model.approval_id, model.candidates = "keep-build", "keep-approval", ["keep"]
    with pytest.raises(StudioError) as caught:
        model.describe_source(source.source_id, "stale draft", expected)
    assert caught.value.code == "REVISION_CONFLICT"
    assert model.sources()[0].manifest["description"] == "newer description"
    assert (model.build_id, model.approval_id, model.candidates) == (
        "keep-build",
        "keep-approval",
        ["keep"],
    )


def test_description_cannot_follow_a_project_switch(tmp_path):
    model, project, source = setup(tmp_path)
    expected = model.project()
    model.create_project("另一專案", "另一目標", "另一條件")
    with pytest.raises(StudioError) as caught:
        model.describe_source(source.source_id, "wrong project", expected)
    assert caught.value.code == "REVISION_CONFLICT"
    with model.service() as studio:
        assert studio.sources.get(project.project_id, source.source_id).version == source.version


def test_invalid_description_keeps_original_version_and_cached_acceptance(tmp_path):
    model, _, source = setup(tmp_path)
    model.build_id, model.approval_id = "kept-build", "kept-approval"
    with pytest.raises(StudioError) as caught:
        model.describe_source(source.source_id, "x" * 8001, model.project())
    assert caught.value.code == "INPUT_INVALID"
    assert model.sources()[0].version == source.version
    assert (model.build_id, model.approval_id) == ("kept-build", "kept-approval")


def test_preview_description_is_bounded_and_does_not_make_thousands_of_label_lines():
    source = SimpleNamespace(
        name="input.txt", version=2, manifest={"description": ("line\n" * 3000)}
    )
    caption = source_caption(source)
    assert caption.count("\n") == 1
    assert len(caption) < 300 and "完整內容" in caption


def test_annotation_change_keeps_published_files_and_requires_new_acceptance(tmp_path):
    from compiler_helpers import runtime_python
    from test_governance import verified_approval
    from workflow_studio.governance.release import Releases
    from workflow_studio.verifier.golden_cases import GoldenCases

    studio, project, build, cases, _, policy, approval = verified_approval(tmp_path)
    try:
        installed = tmp_path / "published"
        Releases(studio).publish(project.project_id, approval["approval_id"], installed, policy)
        before = {
            p.relative_to(installed): p.read_bytes() for p in installed.rglob("*") if p.is_file()
        }
        case = GoldenCases(studio).get(project.project_id, cases[0]["case_id"])
        source_id = case["specification"]["input"]["source_id"]
        model = StudioUIModel(tmp_path / "studio", runtime_python=runtime_python())
        model.select_project(project.project_id)
        model.describe_source(source_id, "updated annotation", model.project())
        with pytest.raises(StudioError):
            Releases(studio).publish(project.project_id, approval["approval_id"], installed, policy)
        assert before == {
            p.relative_to(installed): p.read_bytes() for p in installed.rglob("*") if p.is_file()
        }
        assert studio.projects.get_revision(project.project_id, build["revision_id"]).ir
    finally:
        studio.close()
