import sqlite3

import pytest
from compiler_helpers import build_ir
from test_verifier import verification_case
from workflow_studio.compiler.generator import Compiler
from workflow_studio.llm.streaming import EventStore
from workflow_studio.service import StudioService


def events(studio, project_id):
    return EventStore(studio.repository).read(project_id, limit=1000)


def test_generation_and_verification_events_reopen_with_persisted_results(tmp_path):
    studio, project, build, cases, verifier, policy = verification_case(tmp_path)
    report = verifier.verify(project.project_id, build["build_id"], cases, policy)
    assert report["status"] == "passed"
    studio.close()
    with StudioService.open_workspace(tmp_path / "studio") as reopened:
        recorded = events(reopened, project.project_id)
        names = [e["event"] for e in recorded]
        assert names == [
            "generation.started",
            "generation.delta",
            "generation.delta",
            "generation.completed",
            "verification.started",
            "verification.completed",
            "approval.required",
        ]
        completed = recorded[3]
        assert completed["build_id"] == build["build_id"]
        assert completed["package_hash"] == build["package_hash"]
        assert completed["revision_id"] == build["revision_id"]
        assert recorded[-2]["report_id"] == report["report_id"]
        assert recorded[-1]["status"] == "passed"
        assert (
            reopened.repository.connection.execute(
                "SELECT COUNT(*) FROM studio_approvals"
            ).fetchone()[0]
            == 0
        )
        assert (
            EventStore(reopened.repository).read(project.project_id, after=completed["sequence"])
            == recorded[4:]
        )


def test_generation_io_failure_records_last_stage_without_completion(tmp_path, monkeypatch):
    studio, project, revision, policy = build_ir(tmp_path)

    def fail(*args, **kwargs):
        raise OSError("private-file-sentinel")

    monkeypatch.setattr("workflow_studio.compiler.generator.write_wheel", fail)
    with pytest.raises(OSError):
        studio.compile(project.project_id, revision.revision_id, policy)
    recorded = events(studio, project.project_id)
    assert recorded[-1]["event"] == "generation.failed"
    assert recorded[-1]["last_completed_stage"] == "validated"
    assert "private-file-sentinel" not in str(recorded)
    assert not any(e["event"] == "generation.completed" for e in recorded)
    assert (
        studio.repository.connection.execute("SELECT COUNT(*) FROM studio_builds").fetchone()[0]
        == 0
    )
    studio.close()


def test_failed_business_verification_does_not_request_approval(tmp_path):
    studio, project, build, cases, verifier, policy = verification_case(tmp_path, expected="wrong")
    report = verifier.verify(project.project_id, build["build_id"], cases, policy)
    assert report["status"] == "failed"
    recorded = events(studio, project.project_id)
    assert recorded[-1]["event"] == "verification.completed"
    assert recorded[-1]["status"] == "failed"
    assert recorded[-1]["report_id"] == report["report_id"]
    assert not any(e["event"] == "approval.required" for e in recorded)
    studio.close()


def test_generation_result_read_failure_cannot_follow_a_committed_success(tmp_path, monkeypatch):
    studio, project, revision, policy = build_ir(tmp_path)
    compiler = Compiler(studio)

    def fail(*args):
        raise sqlite3.OperationalError("injected result read failure")

    monkeypatch.setattr(compiler, "get", fail)
    with pytest.raises(sqlite3.OperationalError):
        compiler.compile(project.project_id, revision.revision_id, policy)
    recorded = events(studio, project.project_id)
    assert not any(e["event"] == "generation.completed" for e in recorded)
    assert recorded[-1]["event"] == "generation.failed"
    assert (
        studio.repository.connection.execute("SELECT COUNT(*) FROM studio_builds").fetchone()[0]
        == 0
    )
    studio.close()


@pytest.mark.parametrize("operation", ["generation", "verification"])
def test_completion_event_and_result_commit_atomically(tmp_path, operation):
    studio, project, build, cases, verifier, policy = verification_case(tmp_path)
    before = events(studio, project.project_id)
    studio.repository.connection.execute(
        "CREATE TRIGGER fail_completion BEFORE INSERT ON studio_events "
        f"WHEN NEW.event='{operation}.completed' "
        "BEGIN SELECT RAISE(ABORT, 'injected event failure'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        if operation == "generation":
            studio.compile(project.project_id, build["revision_id"], policy)
        else:
            verifier.verify(project.project_id, build["build_id"], cases, policy)
    recorded = events(studio, project.project_id)[len(before) :]
    assert not any(e["event"].endswith(".completed") for e in recorded)
    assert not any(e["event"] == "approval.required" for e in recorded)
    assert recorded[-1]["event"] == operation + ".failed"
    assert (
        studio.repository.connection.execute("SELECT COUNT(*) FROM studio_builds").fetchone()[0]
        == 1
    )
    assert (
        studio.repository.connection.execute(
            "SELECT COUNT(*) FROM studio_verifications"
        ).fetchone()[0]
        == 0
    )
    assert Compiler(studio).get(project.project_id, build["build_id"])["status"] == "candidate"
    studio.close()
