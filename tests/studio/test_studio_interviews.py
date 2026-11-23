import hashlib
import json
import sqlite3
import subprocess
import sys

import pytest
from workflow_studio.contracts import StudioError
from workflow_studio.conversation.interviews import UserReply
from workflow_studio.service import StudioService
from workflow_studio.storage import sqlite as storage


def confirm(studio, state, key, value, request="user-answer"):
    reply = UserReply(value, "host-message-8", state["session_id"], key, state["revision"], request)
    return studio.interviews.answer(
        state["project_id"],
        state["revision"],
        request,
        key,
        value,
        status="confirmed",
        user_reply=reply,
    )


def test_process_exit_and_reopen_with_history_and_idempotent_answer(tmp_path):
    with StudioService.open_workspace(tmp_path) as studio:
        state = studio.interviews.create("教案", "整理課程")
        saved = confirm(studio, state, "goal", "整理兩份教案")
        assert confirm(studio, state, "goal", "整理兩份教案") == saved
        with pytest.raises(StudioError, match="同一請求"):
            confirm(studio, state, "goal", "其他內容")
        assert studio.projects.get(state["project_id"]).goal["description"] == "整理兩份教案"
        with pytest.raises(StudioError, match="已有更新"):
            confirm(studio, state, "goal", "新的", request="different")
        assert len(studio.interviews.history(state["project_id"])) == 2
    code = """
import json, sys
from workflow_studio.service import StudioService
with StudioService.open_workspace(sys.argv[1]) as s:
    print(json.dumps(s.interviews.get(sys.argv[2])))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code, str(tmp_path), state["project_id"]],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == saved


def test_declared_actor_cannot_turn_host_draft_into_confirmation(tmp_path):
    with StudioService.open_workspace(tmp_path) as studio:
        s = studio.interviews.create("訪談", "建立報表")
        for reply in [
            None,
            {"actor": "user"},
            UserReply("A", "text", "other", "rules", s["revision"], "r"),
        ]:
            with pytest.raises(StudioError) as exc:
                studio.interviews.answer(
                    s["project_id"],
                    s["revision"],
                    "r",
                    "rules",
                    "A",
                    status="confirmed",
                    user_reply=reply,
                )
            assert exc.value.code == "USER_CONFIRMATION_REQUIRED"
        assert studio.interviews.get(s["project_id"]) == s
        draft = studio.interviews.answer(
            s["project_id"],
            s["revision"],
            "r",
            "budget",
            "零費用",
            status="defaulted",
            impact="low",
        )
        assert draft["coverage"]["budget"]["status"] == "defaulted"
        assert draft["phase"] != "ready_to_export"


def test_upstream_edit_from_either_api_invalidates_only_dependent_answers(tmp_path):
    with StudioService.open_workspace(tmp_path) as studio:
        s = studio.interviews.create("訪談", "建立報表")
        s = confirm(studio, s, "quality", "金額一致", "q")
        s = confirm(studio, s, "rules", "保留所有列", "r1")
        s = confirm(studio, s, "quality", "金額一致", "q2")
        s = confirm(studio, s, "cases", "範例一", "c1")
        s = confirm(studio, s, "preferences", "繁體中文", "p")
        s = confirm(studio, s, "rules", "另列例外", "r2")
        assert s["coverage"]["quality"]["status"] == s["coverage"]["cases"]["status"] == "blocked"
        assert s["coverage"]["preferences"]["status"] == "confirmed"
        p = studio.projects.update(s["project_id"], s["revision"], goal={"description": "審核庫存"})
        assert studio.interviews.get(p.project_id)["coverage"]["goal"]["value"] == "審核庫存"
        assert studio.interviews.get(p.project_id)["coverage"]["rules"]["status"] == "blocked"


def test_archive_pause_and_restore_preserve_unanswered_fields(tmp_path):
    with StudioService.open_workspace(tmp_path) as studio:
        s = studio.interviews.create("專案", "任務")
        pid = s["project_id"]
        paused = studio.interviews.pause(pid, s["revision"], "pause")
        with pytest.raises(StudioError, match="恢復"):
            confirm(studio, paused, "goal", "新任務")
        archived = studio.projects.archive(pid, paused["revision"])
        assert studio.interviews.get(pid)["phase"] == "archived"
        restored = studio.projects.restore(pid, archived.version)
        resumed = studio.interviews.pause(pid, restored.version, "resume", resume=True)
        assert resumed["phase"] == "collecting" and resumed["coverage"]["cases"]["value"] is None


def test_concurrent_replies_have_one_winner_and_database_keeps_all_committed_versions(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    with StudioService.open_workspace(tmp_path) as studio:
        initial = studio.interviews.create("相同版本", "整理資料")
    barrier = Barrier(2)

    def write(value):
        with StudioService.open_workspace(tmp_path) as studio:
            barrier.wait(timeout=10)
            try:
                confirm(studio, initial, "rules", value, request=value)
            except StudioError as exc:
                return exc.code
            return "saved"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert set(pool.map(write, ["A", "B"])) == {"REVISION_CONFLICT", "saved"}
    with StudioService.open_workspace(tmp_path) as studio:
        versions = studio.interviews.history(initial["project_id"])
        assert len(versions) == 2
        assert versions[0]["coverage"]["rules"]["status"] == "unknown"
        assert versions[1]["coverage"]["rules"]["status"] == "confirmed"


def test_batch_reply_validates_all_questions_and_replays_at_one_revision(tmp_path):
    from workflow_studio.conversation.facade import next_questions, record_chat_replies

    with StudioService.open_workspace(tmp_path) as studio:
        s = studio.interviews.create("字幕", "轉字幕")
        pid = s["project_id"]
        questions = next_questions(s, ["outputs", "goal"])["questions"]
        text = "保留英文術語，交付繁體 SRT"
        replies = [
            {"domain": q["domain"], "question_id": q["question_id"], "answer_text": quoted}
            for q, quoted in zip(questions, ("交付繁體 SRT", "保留英文術語"), strict=True)
        ]
        with pytest.raises(StudioError, match="原文"):
            record_chat_replies(
                studio,
                pid,
                s["revision"],
                "batch",
                text,
                [replies[0], {**replies[1], "answer_text": "杜撰的需求"}],
            )
        assert studio.interviews.get(pid) == s
        saved = record_chat_replies(studio, pid, s["revision"], "batch", text, replies)
        assert saved["revision"] == s["revision"] + 1
        assert saved["coverage"]["outputs"]["status"] == "confirmed"
        assert saved["coverage"]["outputs"]["user_text"] == text
        assert saved["coverage"]["review"]["status"] == "unknown"
        assert record_chat_replies(studio, pid, s["revision"], "batch", text, replies) == saved
        with pytest.raises(StudioError, match="已有更新"):
            record_chat_replies(studio, pid, s["revision"], "later", text, replies)


def test_v14_project_upgrades_and_backup_restores_in_empty_directory(tmp_path, monkeypatch):
    previous = tmp_path / "old"
    with monkeypatch.context() as m:
        m.setattr(storage, "SCHEMA_VERSION", 14)
        with StudioService.open_workspace(previous) as studio:
            p = studio.projects.create("舊專案", {"description": "舊目標"})
            old_path = studio.repository.path
    with sqlite3.connect(old_path.as_uri() + "?mode=ro", uri=True) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 14
    with StudioService.open_workspace(previous) as upgraded:
        assert upgraded.repository.schema_version == storage.SCHEMA_VERSION
        assert upgraded.projects.get(p.project_id) == p
        (snapshot,) = upgraded.repository.path.parent.glob("backups/before-v014-*.sqlite3")
        assert (
            hashlib.sha256(snapshot.read_bytes()).hexdigest()
            == json.loads(snapshot.with_suffix(".json").read_bytes())["sha256"]
        )
        assert (
            upgraded.interviews.start(p.project_id, p.version)["coverage"]["goal"]["status"]
            == "inferred"
        )
    restored = storage.SQLiteRepository.restore(snapshot, tmp_path / "restored")
    with sqlite3.connect(restored.as_uri() + "?mode=ro", uri=True) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 14
        assert db.execute("SELECT name FROM studio_projects").fetchone()[0] == p.name
