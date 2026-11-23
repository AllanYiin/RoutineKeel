import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image
from workflow_studio.contracts import StudioError
from workflow_studio.conversation.facade import (
    dispatch,
    next_question,
    next_questions,
    preview,
)
from workflow_studio.service import StudioService

from workflow_studio import __main__ as cli


def test_retry_creating_project_does_not_create_a_second_interview(tmp_path):
    with StudioService.open_workspace(tmp_path) as studio:
        params = {"name": "重試", "task": "同一份工作", "request_id": "start-1"}
        first = dispatch(studio, tmp_path, "create", params)
        assert dispatch(studio, tmp_path, "create", params) == first
        with pytest.raises(StudioError, match="不同任務"):
            dispatch(studio, tmp_path, "create", {**params, "task": "另一份工作"})
        assert len(studio.projects.list()) == 1


def test_text_reply_in_desktop_conversation_preserves_scope_and_words(tmp_path):
    with StudioService.open_workspace(tmp_path) as studio:
        state = dispatch(studio, tmp_path, "create", {"name": "案例", "task": "分類文件"})
        assert next_question(state)["next_actions"][0] == "prepare_blueprint"
        assert next_questions(state)["questions"] == []
        question = next_question(state, "goal")
        params = {
            "project_id": state["project_id"],
            "domain": "goal",
            "expected_revision": question["revision"],
            "request_id": "reply-1",
            "question_id": question["question_id"],
            "user_text": "分類報表",
        }
        with pytest.raises(StudioError):
            dispatch(studio, tmp_path, "reply", {**params, "question_id": "stale"})
        saved = dispatch(studio, tmp_path, "reply", params)
        assert dispatch(studio, tmp_path, "reply", params) == saved
        assert (
            saved["coverage"]["goal"]["value"]
            == saved["coverage"]["goal"]["user_text"]
            == "分類報表"
        )
        assert next_question(saved)["next_actions"][0] == "prepare_blueprint"
        assert saved["coverage"]["goal"]["status"] == "confirmed"
        with pytest.raises(StudioError):
            dispatch(
                studio, tmp_path, "reply", {**params, "request_id": "new", "user_text": "過期"}
            )
        shown = next_question(saved, "rules")
        declined = dispatch(
            studio,
            tmp_path,
            "reply",
            {
                "project_id": state["project_id"],
                "domain": "rules",
                "expected_revision": saved["revision"],
                "question_id": shown["question_id"],
                "user_text": "這一題先留著",
                "request_id": "later",
                "response_kind": "defer",
            },
        )
        assert declined["coverage"]["rules"]["status"] == "blocked"


def test_stream_flushes_each_delta_before_eof(tmp_path):
    command = [
        sys.executable,
        "-I",
        "-X",
        "utf8",
        "-m",
        "workflow_studio",
        "--workspace",
        str(tmp_path),
        "interview",
        "stream",
    ]
    with (
        subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8"
        ) as proc,
        ThreadPoolExecutor(max_workers=1) as reader,
    ):
        try:
            for index, part in enumerate(["文字", "增量"]):
                proc.stdin.write(json.dumps({"text": part}) + "\n")
                proc.stdin.flush()
                line = reader.submit(proc.stdout.readline).result(timeout=10)
                assert json.loads(line) == {"event": "delta", "sequence": index, "text": part}
            proc.stdin.close()
            assert proc.wait(timeout=10) == 0
            assert json.loads(proc.stdout.readline()) == {"event": "completed"}
        finally:
            if proc.poll() is None:
                proc.kill()
    assert list(tmp_path.iterdir()) == []


def test_file_previews_show_source_dimensions_and_local_fallback(tmp_path):
    (tmp_path / "note.txt").write_text("待確認內容", encoding="utf-8")
    assert "待確認內容" in preview(tmp_path, "note.txt")["text"]
    Image.new("RGB", (40, 10)).save(tmp_path / "image.png")
    image = preview(tmp_path, "image.png")
    assert (image["width"], image["height"]) == (40, 10)
    (tmp_path / "clip.mp4").write_bytes(b"fixture")
    assert preview(tmp_path, "clip.mp4")["local_uri"] == (tmp_path / "clip.mp4").as_uri()
    (tmp_path / "attachment.bin").write_bytes(b"opaque")
    fallback = preview(tmp_path, "attachment.bin")
    assert fallback["kind"] == "metadata" and fallback["local_uri"] == tmp_path.as_uri()
    with pytest.raises(StudioError):
        preview(tmp_path, "../outside")


def test_host_cannot_self_confirm_by_setting_status(tmp_path, capsys):
    args = ["--workspace", str(tmp_path), "interview"]
    assert (
        cli.main([*args, "create", "--payload", json.dumps({"name": "文字", "task": "任務"})]) == 0
    )
    state = json.loads(capsys.readouterr().out)["result"]
    params = {
        "project_id": state["project_id"],
        "expected_revision": state["revision"],
        "request_id": "r",
        "domain": "rules",
        "value": "允許",
        "status": "confirmed",
    }
    assert cli.main([*args, "answer", "--payload", json.dumps(params)]) == 2
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "USER_CONFIRMATION_REQUIRED"
