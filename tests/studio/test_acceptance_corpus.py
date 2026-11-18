import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest
from acceptance_oracle import evaluate
from test_image_discovery import VisionFixture
from workflow_studio.discovery.images import context_from_messages
from workflow_studio.llm.usage import Budget
from workflow_studio.registry.policy import ToolPolicy
from workflow_studio.service import StudioService

ROOT = Path(__file__).parent / "fixtures/acceptance_v1"
CATALOG = json.loads((ROOT / "catalog.json").read_text(encoding="utf-8"))


def read(relative):
    path = ROOT / relative
    assert path.resolve().is_relative_to(ROOT.resolve()) and not path.is_symlink()
    return path.read_bytes()


def test_twenty_cases_are_fixed_and_no_acceptance_result_is_fabricated():
    cases = CATALOG["cases"]
    assert len(cases) == len({c["id"] for c in cases}) == 20
    assert Counter(c["mode"] for c in cases) == {"text": 5, "mermaid": 5, "folder": 5, "image": 5}
    assert sum(len(c["training"]) for c in cases) == 21
    assert sum(len(c["held_out"]) for c in cases) == 40
    for case in cases:
        assert case["acceptance_status"] == "not_run"
        for relative, expected_hash in case["files"].items():
            assert hashlib.sha256(read(relative)).hexdigest() == expected_hash
        observed = {
            p.relative_to(ROOT).as_posix() for p in (ROOT / case["id"]).rglob("*") if p.is_file()
        }
        assert observed == set(case["files"])
        visible = {
            p.relative_to(ROOT).as_posix()
            for folder in case["visible_sources"]
            for p in (ROOT / folder).rglob("*")
            if p.is_file()
        }
        held = {pair[key] for pair in case["held_out"] for key in ("input", "expected")}
        assert not visible & held
        trained_inputs = {read(pair["input"]) for pair in case["training"]}
        assert all(read(pair["input"]) not in trained_inputs for pair in case["held_out"])
    results = json.loads((ROOT / "results-template.json").read_text(encoding="utf-8"))
    assert results["required_independent_successes"] == 16
    assert (
        results["dataset_sha256"]
        == hashlib.sha256((ROOT / "catalog.json").read_bytes()).hexdigest()
    )
    assert all(c["status"] == "not_run" and not c["held_out_report_ids"] for c in results["cases"])


@pytest.mark.parametrize("case", CATALOG["cases"], ids=lambda c: c["id"])
def test_fixed_expected_outputs_match_independent_business_oracle(case):
    for pair in case["training"] + case["held_out"]:
        actual, expected = (
            evaluate(case["evaluator_rule"], read(pair["input"])),
            read(pair["expected"]),
        )
        if case["comparison"] == "json_equal":
            assert json.loads(actual) == json.loads(expected)
        else:
            assert actual == expected


@pytest.mark.parametrize("case", CATALOG["cases"], ids=lambda c: c["id"])
def test_visible_sources_are_previewable_and_disclosure_excludes_held_out(case, tmp_path):
    with StudioService.open_workspace(tmp_path) as studio:
        project = studio.projects.create(case["title"], case["goal"])
        selected = []
        visible_paths = {}
        for relative in case["visible_sources"]:
            source = studio.sources.import_path(
                project.project_id, studio.projects.get(project.project_id).version, ROOT / relative
            )
            selected.append(source.source_id)
            visible_paths[source.source_id] = relative
            assert all(
                f["preview"]["kind"] in {"text", "mermaid", "image"}
                for f in source.manifest["files"]
            )
        studio.tools.seed()
        provider = VisionFixture()
        prepared = studio.discovery(
            provider, Budget(3, 2_000_000, 8000, 1_000_000, 1_000_000), ToolPolicy("windows")
        ).prepare(project.project_id, selected)
        context = context_from_messages(json.loads(prepared.messages_json))
        sent = {visible_paths[s["source_id"]] + "/" + s["path"] for s in context["sources"]}
        held = {pair[key] for pair in case["held_out"] for key in ("input", "expected")}
        assert not sent & held
        assert all("/held_out/" not in path for path in sent)
        assert all(pair[key] in sent for pair in case["training"] for key in ("input", "expected"))
        assert provider.calls == 0
