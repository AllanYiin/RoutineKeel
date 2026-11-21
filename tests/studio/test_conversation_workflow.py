import pytest
from compiler_helpers import runtime_python
from workflow_studio.contracts import StudioError
from workflow_studio.conversation.session import StudioSession, analysis_text

pytest.importorskip("PIL", reason="Desktop Studio requires its declared image dependencies")


def case_folder(tmp_path):
    folder = tmp_path / "成功案例"
    (folder / "input").mkdir(parents=True)
    (folder / "output").mkdir()
    (folder / "input/a.txt").write_text("一份成功範例", encoding="utf-8")
    (folder / "output/a.txt").write_text("一份成功範例", encoding="utf-8")
    return folder


def test_model_end_to_end_without_editing_code_or_configuration(tmp_path):
    model = StudioSession(tmp_path / "studio", runtime_python=runtime_python())
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
    reopened = StudioSession(tmp_path / "studio", runtime_python=runtime_python())
    reopened.select_project(p.project_id)
    assert reopened.revision().ir == changed.ir
    assert reopened.release_history()


def test_analysis_text_is_visible_without_ir_json():
    assert analysis_text('{"analysis":"逐步分析') == "逐步分析"
    assert analysis_text('{"analysis":"完成","ir":{"nodes":[]}}') == "完成"
    assert analysis_text('{"analysis":"hello\\u4e') == "hello"


def test_model_configuration_reopens_without_storing_credentials(tmp_path):
    model = StudioSession(tmp_path)
    project = model.create_project("設定", "描述工作", "有可驗證成果")
    model.configure_provider("https://example.invalid/chat", "fixture", 0.5, 1.0, 2.0)
    reopened = StudioSession(tmp_path)
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
    model = StudioSession(tmp_path / "studio", runtime_python=runtime_python())
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
