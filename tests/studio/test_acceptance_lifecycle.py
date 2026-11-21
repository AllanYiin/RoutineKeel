from test_edge_cases import tree_bytes
from test_governance import verified_approval
from workflow_studio.conversation.session import StudioSession
from workflow_studio.governance.release import Releases


def test_third_revision_reopens_with_sources_evidence_and_unchanged_published_package(tmp_path):
    studio, p, build, _, verifier, policy, approval = verified_approval(tmp_path)
    root = tmp_path / "installed"
    first = Releases(studio).publish(p.project_id, approval["approval_id"], root, policy)
    published = tree_bytes(root)
    original = studio.projects.get_revision(p.project_id, build["revision_id"])
    studio.close()
    model = StudioSession(tmp_path / "studio", runtime_python=verifier.runtime_python)
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
    reopened = StudioSession(tmp_path / "studio", runtime_python=verifier.runtime_python)
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
