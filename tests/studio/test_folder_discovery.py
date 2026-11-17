import json
import os

import pytest
from workflow_studio.discovery.evidence_graph import EvidenceGraphs
from workflow_studio.discovery.folder import FolderDiscovery
from workflow_studio.registry.policy import ToolPolicy
from workflow_studio.service import StudioService
from workflow_studio.verifier.golden_cases import GoldenCases

pytest.importorskip("PIL", reason="Folder discovery fixtures require Studio source dependencies")


def folder_studio(tmp_path, entries, timestamps=None):
    folder = tmp_path / "案例"
    folder.mkdir()
    for relative, text in entries.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")
        if timestamps and relative in timestamps:
            os.utime(path, (timestamps[relative], timestamps[relative]))
    studio = StudioService.open_workspace(tmp_path / "workspace")
    p = studio.projects.create(
        "成功案例", {"description": "複製並整理檔案", "success_criteria": ["符合成功案例"]}
    )
    source = studio.sources.import_path(p.project_id, 1, folder)
    studio.tools.seed()
    return studio, p, source, folder


def test_known_folder_candidates_golden_case_and_reopen(tmp_path):
    studio, p, source, _ = folder_studio(
        tmp_path, {"input/a.txt": "same\n", "output/a.txt": "same\n"}
    )
    discovery = FolderDiscovery(studio)
    snapshot = discovery.analyze(p.project_id, source.source_id, 2)
    assert all(e["confidence_label"] == "inferred" for e in snapshot["graph"]["relations"])
    candidates = discovery.candidate_irs(p.project_id, snapshot["graph_id"], ToolPolicy("windows"))
    assert len(candidates) >= 2
    assert all(c["ir"]["evidence"][0]["confidence_label"] == "inferred" for c in candidates)
    cases = GoldenCases(studio)
    case = cases.from_candidate(p.project_id, 3, candidates[0])
    assert cases.bytes(p.project_id, case["case_id"]) == (b"same\n", b"same\n")
    studio.close()
    with StudioService.open_workspace(tmp_path / "workspace") as reopened:
        assert (
            EvidenceGraphs(reopened.repository).get(p.project_id, snapshot["graph_id"]) == snapshot
        )
        assert GoldenCases(reopened).bytes(p.project_id, case["case_id"])[0] == b"same\n"


def test_only_final_output_does_not_invent_inputs(tmp_path):
    studio, p, source, _ = folder_studio(tmp_path, {"output/final.txt": "result"})
    discovery = FolderDiscovery(studio)
    snapshot = discovery.analyze(p.project_id, source.source_id, 2)
    assert snapshot["graph"]["relations"] == []
    assert snapshot["graph"]["unknowns"]
    assert discovery.candidate_irs(p.project_id, snapshot["graph_id"], ToolPolicy("windows")) == []
    studio.close()


def test_same_name_different_content_and_private_logs(tmp_path):
    studio, p, source, _ = folder_studio(
        tmp_path,
        {
            "input/a.txt": "input",
            "output/a.txt": "unrelated output",
            "run.log": "secret-token-not-for-graph; input/a.txt then output/a.txt",
        },
    )
    snapshot = FolderDiscovery(studio).analyze(p.project_id, source.source_id, 2)
    assert snapshot["graph"]["relations"] == []
    assert "secret-token-not-for-graph" not in json.dumps(snapshot)
    assert snapshot["graph"]["log_observations"][0]["referenced_files"] == [
        "input/a.txt",
        "output/a.txt",
    ]
    studio.close()


def test_misleading_timestamp_cannot_confirm_causality(tmp_path):
    studio, p, source, folder = folder_studio(
        tmp_path,
        {"input/a.txt": "sample", "output/a.txt": "sample"},
        timestamps={"input/a.txt": 200000, "output/a.txt": 100000},
    )
    os.utime(folder / "input/a.txt", (200000, 200000))
    os.utime(folder / "output/a.txt", (100000, 100000))
    snapshot = FolderDiscovery(studio).analyze(p.project_id, source.source_id, 2)
    assert snapshot["graph"]["relations"][0]["sample_match"] is True
    assert snapshot["graph"]["relations"][0]["confidence_label"] == "inferred"
    assert "因果" in snapshot["graph"]["timestamps"]
    assert snapshot["graph"]["time_observations"][0]["input_before_output"] is False
    studio.close()


def test_intermediate_chain_and_bounded_work(tmp_path):
    studio, p, source, _ = folder_studio(
        tmp_path,
        {
            "input/a.txt": " a \r\n",
            "intermediate/a.txt": "a\n",
            "output/a.txt": "a\n",
        },
    )
    discovery = FolderDiscovery(studio)
    snapshot = discovery.analyze(p.project_id, source.source_id, 2, max_comparisons=3)
    assert snapshot["graph"]["comparisons"] <= 3
    assert (
        len(
            discovery.candidate_irs(
                p.project_id, snapshot["graph_id"], ToolPolicy("windows"), max_candidates=2
            )
        )
        <= 2
    )
    studio.close()


def test_user_relation_decision_keeps_prior_graph(tmp_path):
    studio, p, source, _ = folder_studio(
        tmp_path, {"input/a.txt": "sample", "output/a.txt": "sample"}
    )
    snapshot = FolderDiscovery(studio).analyze(p.project_id, source.source_id, 2)
    graphs = EvidenceGraphs(studio.repository)
    relation_id = snapshot["graph"]["relations"][0]["relation_id"]
    updated = graphs.revise_relation(
        p.project_id, snapshot["graph_id"], 3, relation_id, decision="confirm", actor="fixture user"
    )
    assert updated["graph"]["relations"][0]["confidence_label"] == "human_confirmed"
    assert graphs.get(p.project_id, snapshot["graph_id"], 1) == snapshot
    removed = graphs.revise_relation(
        p.project_id, snapshot["graph_id"], 4, relation_id, decision="remove", actor="fixture user"
    )
    assert not any(e["relation_id"] == relation_id for e in removed["graph"]["relations"])
    graphs.archive(p.project_id, snapshot["graph_id"], 5)
    assert graphs.list(p.project_id) == []
    studio.close()


def test_relation_replacement_selects_existing_alternative(tmp_path):
    studio, p, source, _ = folder_studio(
        tmp_path, {"input/a.txt": "sample\n", "output/a.txt": "sample\n"}
    )
    snapshot = FolderDiscovery(studio).analyze(p.project_id, source.source_id, 2)
    original, alternative = snapshot["graph"]["relations"][:2]
    updated = EvidenceGraphs(studio.repository).replace_relation(
        p.project_id,
        snapshot["graph_id"],
        3,
        original["relation_id"],
        alternative["relation_id"],
        actor="fixture user",
    )
    replacement = next(
        r for r in updated["graph"]["relations"] if r["relation_id"] == original["relation_id"]
    )
    assert replacement["tool"] == alternative["tool"]
    assert replacement["confidence_label"] == "human_confirmed"
    studio.close()
