import pytest
from test_conversation_workflow import case_folder
from workflow_studio.contracts import StudioError
from workflow_studio.conversation.session import StudioSession
from workflow_studio.verifier.golden_cases import GoldenCases


def test_invalid_case_rolls_back_both_sources_and_project_version(tmp_path):
    model = StudioSession(tmp_path / "studio")
    before = model.create_project("試做", "複製資料", "符合固定範例")
    source, expected = tmp_path / "input.txt", tmp_path / "expected.txt"
    source.write_text("input")
    expected.write_text('{"x":1,"x":2}')
    with pytest.raises(StudioError, match="重複"):
        model.add_case(source, expected, comparison="json_equal")
    assert model.project() == before
    assert model.sources() == []
    assert model.cases() == []
    with model.service() as studio:
        assert (
            studio.repository.connection.execute(
                "SELECT COUNT(*) FROM studio_source_blobs"
            ).fetchone()[0]
            == 0
        )
    expected.write_text('{"x":1}')
    case = model.add_case(source, expected, comparison="json_equal")
    assert case["specification"]["comparison"] == "json_equal"
    assert len(model.sources()) == 2


def test_candidate_case_failure_rolls_back_saved_ir(tmp_path, monkeypatch):
    model = StudioSession(tmp_path / "studio")
    model.create_project("候選", "複製資料", "符合固定範例")
    source = model.import_path(case_folder(tmp_path))
    candidates, _ = model.folder_candidates(source.source_id)
    assert candidates
    before = model.project()
    with model.service() as studio:
        revisions = studio.projects.revisions(before.project_id)

    def fail(*args, **kwargs):
        raise StudioError("SOURCE_CORRUPT", "injected case save failure")

    monkeypatch.setattr(GoldenCases, "from_candidate", fail)
    with pytest.raises(StudioError, match="injected"):
        model.accept_candidate(0)
    assert model.project() == before
    with model.service() as studio:
        assert studio.projects.revisions(before.project_id) == revisions
    assert model.cases() == []
