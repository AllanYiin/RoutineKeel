import copy

import pytest
from test_folder_discovery import folder_studio
from workflow_studio.contracts import StudioError
from workflow_studio.discovery.folder import FolderDiscovery
from workflow_studio.registry.policy import ToolPolicy
from workflow_studio.verifier.golden_cases import GoldenCases


def test_golden_case_versions_archival_and_hash_validation(tmp_path):
    studio, p, source, _ = folder_studio(
        tmp_path, {"input/a.txt": "sample", "output/a.txt": "sample"}
    )
    discovery = FolderDiscovery(studio)
    graph = discovery.analyze(p.project_id, source.source_id, 2)
    candidate = discovery.candidate_irs(p.project_id, graph["graph_id"], ToolPolicy("windows"))[0]
    cases = GoldenCases(studio)
    first = cases.from_candidate(p.project_id, 3, candidate)
    spec = copy.deepcopy(first["specification"])
    spec["name"] = "new name"
    second = cases.save(p.project_id, 4, spec, case_id=first["case_id"])
    assert second["version"] == 2
    assert cases.get(p.project_id, first["case_id"], 1) == first
    spec["input"]["sha256"] = "0" * 64
    with pytest.raises(StudioError):
        cases.save(p.project_id, 5, spec)
    cases.archive(p.project_id, first["case_id"], 5)
    assert cases.list(p.project_id) == []
    with pytest.raises(StudioError):
        cases.bytes(p.project_id, first["case_id"])
    assert len(cases.list(p.project_id, include_archived=True)) == 1
    studio.close()
