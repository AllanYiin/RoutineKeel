import pytest
from test_discovery import FixtureProvider
from test_edge_cases import tree_bytes
from test_governance import verified_approval
from workflow_studio.contracts import StudioError
from workflow_studio.governance.release import Releases
from workflow_studio.llm.streaming import EventStore
from workflow_studio.llm.usage import Budget, UsageLedger
from workflow_studio.ui.model import StudioUIModel


def test_third_revision_reopens_with_sources_evidence_and_unchanged_published_package(tmp_path):
    studio, p, build, _, verifier, policy, approval = verified_approval(tmp_path)
    root = tmp_path / "installed"
    first = Releases(studio).publish(p.project_id, approval["approval_id"], root, policy)
    published = tree_bytes(root)
    original = studio.projects.get_revision(p.project_id, build["revision_id"])
    studio.close()
    model = StudioUIModel(tmp_path / "studio", runtime_python=verifier.runtime_python)
    model.select_project(p.project_id)
    second = model.decide_evidence(
        model.project().version,
        original.ir["evidence"][0]["id"],
        "reviewer",
        "confirmed fixed copy rule",
    )
    assert second.ir["revision"] == 2
    third = model.edit_node("copy", output_name="third.txt")
    assert third.ir["revision"] == 3 and third.status == "ir_ready"
    sources = [(s.source_id, s.version, s.content_hash) for s in model.sources()]
    decisions = model.evidence_decisions()
    reopened = StudioUIModel(tmp_path / "studio", runtime_python=verifier.runtime_python)
    reopened.select_project(p.project_id)
    assert reopened.revision() == third
    assert [(s.source_id, s.version, s.content_hash) for s in reopened.sources()] == sources
    assert reopened.evidence_decisions() == decisions
    assert reopened.revision().ir["evidence"][0]["verified_by"] == decisions[-1]["decision_id"]
    fourth = reopened.reuse_revision(third.revision_id, reopened.project().version)
    assert fourth.ir["revision"] == 4
    assert reopened.verify()["status"] == "passed"
    with reopened.service() as service:
        assert service.projects.get_revision(p.project_id, third.revision_id) == third
        assert service.projects.get_revision(p.project_id, original.revision_id) == original
        assert Releases(service).current(root, "copy-example")["release_id"] == first["release_id"]
    assert tree_bytes(root) == published


def test_interrupted_stream_is_visible_and_reopens_last_good_candidate(tmp_path, monkeypatch):
    from test_project_edits import Variable
    from workflow_studio.ui.app import StudioApp

    studio, p, build, _, verifier, policy, approval = verified_approval(tmp_path)
    root = tmp_path / "installed"
    Releases(studio).publish(p.project_id, approval["approval_id"], root, policy)
    published = tree_bytes(root)
    original = studio.projects.get_revision(p.project_id, build["revision_id"])
    studio.close()
    model = StudioUIModel(tmp_path / "studio", runtime_python=verifier.runtime_python)
    model.select_project(p.project_id)
    previous = model.reuse_revision(original.revision_id, model.project().version)
    model.provider, model.budget = (
        FixtureProvider(interrupted=True),
        Budget(3, 1_000_000, 8000, 1_000_000, 1_000_000),
    )
    prepared = model.prepare_analysis([previous.ir["evidence"][0]["source_id"]])
    streamed = []
    with pytest.raises(StudioError) as caught:
        model.analyze(prepared, streamed.append, lambda: False)
    assert caught.value.code == "LLM_STREAM_INTERRUPTED"
    assert any(event["event"] == "analysis.delta" for event in streamed)
    assert not any(event["event"] == "analysis.completed" for event in streamed)
    shown = []
    app = StudioApp.__new__(StudioApp)
    app.status, app.root = Variable(), object()
    monkeypatch.setattr(
        "workflow_studio.ui.app.messagebox.showerror",
        lambda title, message, **kwargs: shown.append(message),
    )
    app.error(caught.value)
    assert "未完成" in app.status.get() and shown == [app.status.get()]
    reopened = StudioUIModel(tmp_path / "studio", runtime_python=verifier.runtime_python)
    reopened.select_project(p.project_id)
    assert reopened.revision() == previous
    with reopened.service() as service:
        events = EventStore(service.repository).read(p.project_id)
        assert (
            events[-1]["event"] == "analysis.failed"
            and events[-1]["code"] == "LLM_STREAM_INTERRUPTED"
        )
        receipts = UsageLedger(service.repository).summary(p.project_id)["receipts"]
        assert receipts[-1]["status"] == "interrupted"
        assert len(service.projects.revisions(p.project_id)) == 2
    assert tree_bytes(root) == published
