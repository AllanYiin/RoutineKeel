import zipfile

import pytest
from workflow_studio.contracts import StudioError
from workflow_studio.conversation.session import StudioSession


def setup(tmp_path):
    model = StudioSession(tmp_path / "studio")
    model.create_project("案例編輯", "整理資料", "符合樣本")
    original = tmp_path / "input.txt"
    original.write_text('{"x":1}', encoding="utf-8")
    case = model.add_case(original, original)
    return model, case


def test_edit_and_restore_case_keeps_every_source_and_comparison_version(tmp_path):
    model, first = setup(tmp_path)
    new = tmp_path / "replacement.json"
    new.write_text('{ "x": 2 }', encoding="utf-8")
    model.approval_id = "old-approval"
    second = model.update_case(
        first["case_id"],
        1,
        model.project().version,
        name="第二版",
        comparison="json_equal",
        input_path=new,
        expected_path=new,
    )
    assert second["version"] == 2
    assert model.approval_id is None
    assert model.case_history(first["case_id"])[0] == first
    assert '{"x":1}' in model.case_preview(first["case_id"], 1)["input"]
    assert '{ "x": 2 }' in model.case_preview(first["case_id"], 2)["expected"]
    model.archive_case(first["case_id"], model.project().version)
    assert model.cases() == []
    assert len(model.cases(include_archived=True)) == 1
    fourth = model.update_case(
        first["case_id"],
        1,
        model.project().version,
        name="從原始案例重開",
        comparison="exact_bytes",
    )
    assert fourth["version"] == 4 and fourth["status"] == "active"
    assert fourth["specification"]["input"] == first["specification"]["input"]
    assert len(model.case_history(first["case_id"])) == 4


def test_failed_edit_rolls_back_new_sources_and_rejects_stale_form(tmp_path):
    model, case = setup(tmp_path)
    before = model.project()
    sources = model.sources()
    bad = tmp_path / "bad.json"
    bad.write_text('{"x":1,"x":2}')
    with pytest.raises(StudioError):
        model.update_case(
            case["case_id"],
            1,
            before.version,
            name="錯誤",
            comparison="json_equal",
            input_path=bad,
            expected_path=bad,
        )
    assert model.project() == before
    assert model.sources() == sources
    assert model.cases() == [case]
    model.import_text("其他編輯")
    with pytest.raises(StudioError) as exc:
        model.update_case(case["case_id"], 1, before.version, name="過期", comparison="exact_bytes")
    assert exc.value.code == "REVISION_CONFLICT"
    with pytest.raises(StudioError):
        model.archive_case(case["case_id"], before.version)


def test_multi_file_archive_is_not_silently_reduced_to_one_case_file(tmp_path):
    model, case = setup(tmp_path)
    bundle = tmp_path / "two.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("one.txt", "one")
        archive.writestr("two.txt", "two")
    before = model.project()
    with pytest.raises(StudioError, match="一個檔案"):
        model.add_case(bundle, tmp_path / "input.txt")
    with pytest.raises(StudioError, match="一個檔案"):
        model.update_case(
            case["case_id"],
            1,
            before.version,
            name="多檔",
            comparison="exact_bytes",
            input_path=bundle,
        )
    assert model.project() == before
    assert model.cases() == [case]


def test_case_editor_invalidates_old_approval_but_retains_original_report(tmp_path):
    from test_governance import verified_approval
    from workflow_studio.governance.release import Releases
    from workflow_studio.service import StudioService
    from workflow_studio.verifier.report import Verifier

    studio, p, build, cases, verifier, policy, approval = verified_approval(tmp_path)
    report = verifier.get(p.project_id, approval["report_id"])
    studio.close()
    model = StudioSession(tmp_path / "studio")
    model.select_project(p.project_id)
    model.update_case(
        cases[0]["case_id"],
        1,
        model.project().version,
        name="重新定義案例",
        comparison="exact_bytes",
        output_name="result",
    )
    with StudioService.open_workspace(tmp_path / "studio") as studio:
        with pytest.raises(StudioError, match="重新驗證"):
            Releases(studio).publish(
                p.project_id, approval["approval_id"], tmp_path / "installed", policy
            )
        assert Verifier(studio).get(p.project_id, approval["report_id"]) == report
