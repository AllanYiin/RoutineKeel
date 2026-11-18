import platform

import pytest
from compiler_helpers import runtime_python
from test_acceptance_corpus import CATALOG, ROOT
from workflow_studio.discovery.folder import FolderDiscovery
from workflow_studio.governance.approvals import Approvals
from workflow_studio.governance.release import Releases
from workflow_studio.registry.policy import ToolPolicy
from workflow_studio.service import StudioService
from workflow_studio.verifier.golden_cases import GoldenCases
from workflow_studio.verifier.report import Verifier


@pytest.mark.parametrize(
    "case", [c for c in CATALOG["cases"] if c["mode"] == "folder"], ids=lambda c: c["id"]
)
def test_folder_corpus_generates_replays_held_out_and_publishes_without_model(
    case, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "workflow_studio.llm.provider.ChatCompletionsProvider.stream",
        lambda *a, **k: pytest.fail("folder inference must not call a model"),
    )
    with StudioService.open_workspace(tmp_path / "studio") as studio:
        p = studio.projects.create(case["title"], case["goal"])
        studio.tools.seed()
        source = studio.sources.import_path(p.project_id, p.version, ROOT / case["id"] / "training")
        discovery = FolderDiscovery(studio)
        graph = discovery.analyze(
            p.project_id, source.source_id, studio.projects.get(p.project_id).version
        )
        policy = ToolPolicy(
            {"Windows": "windows", "Darwin": "macos"}.get(platform.system(), "linux")
        )
        candidates = discovery.candidate_irs(p.project_id, graph["graph_id"], policy)
        assert candidates
        revision = discovery.accept(
            p.project_id, studio.projects.get(p.project_id).version, candidates[0], policy
        )
        if case["id"] == "C15":
            assert len(revision.ir["nodes"]) == 2
        catalog = GoldenCases(studio)
        for index, pair in enumerate(case["held_out"]):
            refs = {}
            for side in ("input", "expected"):
                imported = studio.sources.import_path(
                    p.project_id, studio.projects.get(p.project_id).version, ROOT / pair[side]
                )
                record = imported.manifest["files"][0]
                refs[side] = {
                    "source_id": imported.source_id,
                    "version": imported.version,
                    "path": record["path"],
                    "sha256": record["sha256"],
                }
            catalog.save(
                p.project_id,
                studio.projects.get(p.project_id).version,
                {
                    "name": f"held-out-{index + 1}",
                    **refs,
                    "comparison": case["comparison"],
                    "output_name": "result",
                },
            )
        build = studio.compile(p.project_id, revision.revision_id, policy)
        bindings = [
            {"case_id": c["case_id"], "version": c["version"], "output": "result"}
            for c in catalog.list(p.project_id)
        ]
        report = Verifier(studio, runtime_python()).verify(
            p.project_id, build["build_id"], bindings, policy
        )
        assert report["status"] == "passed", report
        assert len(report["cases"]) == 3
        approved = Approvals(studio).approve(
            p.project_id,
            build["build_id"],
            build["package_hash"],
            policy,
            actor="engineering fixture",
            reason="training and fixed held-out data passed; not a human campaign result",
        )
        published = Releases(studio).publish(
            p.project_id, approved["approval_id"], tmp_path / "installed", policy
        )
        assert published["state"] == "published"
        assert (
            studio.repository.connection.execute(
                "SELECT count(*) FROM studio_llm_calls"
            ).fetchone()[0]
            == 0
        )
