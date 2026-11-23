import hashlib
import sys
import zipfile

import pytest
from routinekeel_contracts.asset_loading import load_pack
from workflow_studio.patterns.qualification import qualify
from workflow_studio.registry.packs import CatalogDecision
from workflow_studio.service import StudioService

from tools.reference_packs import data_reference, text_reference, write_reference_pack


@pytest.mark.parametrize(
    ("name", "reference", "pattern_ref"),
    [
        ("text", text_reference, "text.review_and_humanize@1.0.0"),
        ("data", data_reference, "data.pre_analysis_reconcile@1.0.0"),
    ],
)
def test_optional_reference_pack_is_reproducible_inert_and_can_replay(
    tmp_path,
    name,
    reference,
    pattern_ref,
):
    archive = write_reference_pack(tmp_path / "pack.zip", name, reference())
    second = write_reference_pack(tmp_path / "second.zip", name, reference())
    assert (
        hashlib.sha256(archive.read_bytes()).digest()
        == hashlib.sha256(second.read_bytes()).digest()
    )
    with zipfile.ZipFile(archive) as bundle:
        assert all(member.endswith(".json") for member in bundle.namelist())
    assert load_pack(archive).manifest.model.pattern_refs == [pattern_ref]
    with StudioService.open_workspace(tmp_path / "studio") as studio:
        registry = studio.tools.packs
        item = registry.register(archive)
        registry.decide(
            CatalogDecision(
                "approve",
                item["ref"],
                item["manifest_hash"],
                item["revision"],
                "fixture",
                "simulated local test approval",
                "local_terminal",
            )
        )
        verdict = qualify(studio, item["ref"], pattern_ref, runtime_python=sys.executable)
        assert verdict["verdict"] == "PASS", verdict
        assert verdict["quality_report"]["human_review_required"]
        assert verdict["provider_calls"] == 0
        assert registry.patterns() == []
