import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest
from routinekeel_contracts.asset_loading import load_asset
from routinekeel_contracts.domain_assets import (
    PatternManifest,
    bind_profile_parameters,
    canonical_json,
)
from test_pattern_registry import decision
from workflow_studio.contracts import StudioError
from workflow_studio.patterns.generalizer import generalize
from workflow_studio.patterns.qualification import qualify
from workflow_studio.patterns.quality import check_reference_quality
from workflow_studio.service import StudioService

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/domain_assets"


def text_pattern():
    pattern = load_asset("pattern", FIXTURES / "pattern.yaml").model.model_dump(mode="json")
    pattern["quality_contract"] = {
        "pattern_checks": [{"kind": "text_fidelity", "source": "text", "output": "text"}]
    }
    pattern["tests"]["required_edges"] = ["normal", "mismatch", "empty"]
    return pattern


def data_pattern():
    pattern = text_pattern()
    ports = {"left": {"type": "array"}, "right": {"type": "array"}}
    outputs = {"status": {"type": "string"}}
    pattern["pattern"].update(id="data.compare", title="金額勾稽", declared_domains=["data"])
    pattern["boundary"].update(inputs=ports, outputs=outputs)
    pattern["graph"] = {
        "inputs": ports,
        "nodes": [
            {
                "id": "compare",
                "kind": "task",
                "capability": "data.reconcile",
                "capability_version": "1.0.0",
                "side_effect": "external",
                "permissions": ["workspace.read"],
                "inputs": {
                    name: {"source": f"input://{name}", "port": port}
                    for name, port in ports.items()
                },
                "outputs": outputs,
            }
        ],
        "outputs": {"status": "artifact://compare/status"},
    }
    pattern["requirements"] = [
        {
            "id": "reconcile",
            "capability": "data.reconcile",
            "version": "1.0.0",
            "required": True,
            "portability": "flexible",
            "execution_loci": ["host_mediated"],
        }
    ]
    pattern["quality_contract"] = {
        "pattern_checks": [
            {
                "kind": "decimal_reconciliation",
                "left": "left",
                "right": "right",
                "key": "id",
                "amount": "amount",
                "absolute_tolerance": "0.000",
                "output": "status",
            }
        ]
    }
    return pattern


def fixtures(pattern, *, simulation=False, false_outcome=False, synthetic_only=False):
    if simulation:
        comparisons = [
            (
                "small_amount",
                "positive",
                "normal",
                [{"id": "A", "amount": "0.10"}],
                [{"id": "A", "amount": "0.100"}],
                "matched",
            ),
            (
                "formatted_amount",
                "positive",
                "normal",
                [{"id": "B", "amount": "1290.02"}],
                [{"id": "B", "amount": "1290.020"}],
                "matched",
            ),
            (
                "mismatch",
                "negative",
                "mismatch",
                [{"id": "A", "amount": "1.01"}],
                [{"id": "A", "amount": "1.00"}],
                "matched" if false_outcome else "mismatched",
            ),
            ("empty", "boundary", "empty", [{"id": "A", "amount": "1.0"}], [], "invalid_input"),
        ]
        cases = [
            (identifier, category, edge, {"left": left, "right": right}, {"status": expected})
            for identifier, category, edge, left, right, expected in comparisons
        ]
    else:
        cases = [
            (identifier, category, edge, {"text": value}, {"text": value})
            for identifier, category, edge, value in [
                ("invoice", "positive", "normal", "發票 47 號，合計 15.20 元 [附件 A]"),
                ("margin", "positive", "normal", "保留 −1.25% 以及援引 [章 2]"),
                ("literal", "negative", "mismatch", "不可補寫任何引用"),
                ("blank", "boundary", "empty", ""),
            ]
        ]
    entries = {"patterns/reference.json": canonical_json(pattern)}
    suite = []
    for identifier, category, edge, arguments, expected in cases:
        origin = {"kind": "synthetic"}
        if category == "positive" and not synthetic_only:
            # Authored reference inputs and independently specified expected records.
            source = f"cases/{identifier}.json"
            raw = canonical_json({"inputs": arguments, "expected": expected})
            entries[source] = raw
            origin = {"kind": "business", "path": source, "sha256": hashlib.sha256(raw).hexdigest()}
        replies = []
        if simulation:
            replies = [
                {"step": "compare", "kind": "authorize_external"},
                {"step": "compare", "kind": "external", "outputs": expected},
            ]
        suite.append(
            {
                "id": identifier,
                "class": category,
                "edge": edge,
                "inputs": arguments,
                "expected": expected,
                "origin": origin,
                "replies": replies,
            }
        )
    entries["cases/index.json"] = canonical_json({"case_schema_version": "1.0", "cases": suite})
    return entries


def write_pack(tmp_path, pattern, **options):
    entries = fixtures(pattern, **options)
    pack = {
        "pack_schema_version": "1.0",
        "pack": {
            "id": "routinekeel.reference",
            "version": "1.0.0",
            "title": "Pattern qualification",
            "domains": ["reference"],
            "publisher": "local",
            "runtime_compatibility": ">=0.3,<0.4",
        },
        "pattern_refs": [pattern["pattern"]["id"] + "@" + pattern["pattern"]["version"]],
        "provenance": {"source": "authored reference cases", "license": "MIT"},
        "assets": {name: hashlib.sha256(body).hexdigest() for name, body in entries.items()},
    }
    path = tmp_path / "reference.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in {"pack.json": canonical_json(pack), **entries}.items():
            archive.writestr(name, body)
    return path


@pytest.mark.parametrize("simulation", [False, True])
def test_qualification_uses_builder_and_real_output_observations(tmp_path, simulation):
    pattern = data_pattern() if simulation else text_pattern()
    source = write_pack(tmp_path, pattern, simulation=simulation)
    with StudioService.open_workspace(tmp_path / "design") as studio:
        registry = studio.tools.packs
        pack = registry.decide(decision("approve", registry.register(source)))
        ref = pack["manifest"]["pattern_refs"][0]
        pending = registry.pattern_status(ref)
        checked = qualify(studio, pack["ref"], ref, runtime_python=sys.executable)
        assert checked["verdict"] == "PASS", checked
        assert checked["provider_calls"] == 0
        assert checked["quality_report"]["human_review_required"] == simulation
        assert checked["conformance_report"]["missing_fixture_preflight"] == (
            "blocked" if simulation else "ready"
        )
        assert all(case["run_id"].startswith("run_") for case in checked["cases"])
        expected_locus = "simulated" if simulation else "local_execution"
        assert {case["external_behavior"] for case in checked["cases"]} == {expected_locus}
        assert len(checked["conformance_report"]["candidate_hash"]) == 64
        fingerprint = checked["qualification_hash"]
        assert registry.trial(ref, fingerprint) == checked
        assert registry.pattern_status(ref) == pending
        accepted = registry.decide_pattern(decision("approve", pending, evidence=fingerprint))
        assert accepted["status"] == "active"


def test_semantic_failure_blocks_pattern_even_when_fixture_exact_match_passes(tmp_path):
    source = write_pack(tmp_path, data_pattern(), simulation=True, false_outcome=True)
    with StudioService.open_workspace(tmp_path / "design") as studio:
        registry = studio.tools.packs
        pack = registry.decide(decision("approve", registry.register(source)))
        ref = pack["manifest"]["pattern_refs"][0]
        checked = qualify(studio, pack["ref"], ref, runtime_python=sys.executable)
        assert checked["conformance_report"]["builder_receipt"]
        assert checked["verdict"] == "FAIL"
        failures = [
            case["id"]
            for case in checked["cases"]
            if any(rule["status"] == "FAIL" for rule in case["quality_checks"])
        ]
        assert failures == ["mismatch"]
        with pytest.raises(StudioError, match="試做收據"):
            registry.decide_pattern(
                decision(
                    "approve", registry.pattern_status(ref), evidence=checked["qualification_hash"]
                )
            )


def test_synthetic_only_suite_cannot_supply_business_evidence(tmp_path):
    source = write_pack(tmp_path, text_pattern(), synthetic_only=True)
    with StudioService.open_workspace(tmp_path / "design") as studio:
        registry = studio.tools.packs
        pack = registry.decide(decision("approve", registry.register(source)))
        with pytest.raises(StudioError, match="兩組不同"):
            qualify(studio, pack["ref"], pack["manifest"]["pattern_refs"][0])


def test_generalization_preserves_contract_and_different_tolerance_requires_variant():
    left, right = data_pattern(), data_pattern()
    right["pattern"]["version"] = "1.1.0"
    report = generalize([left, right], pattern_id="data.reusable", version="2.0.0")
    assert report["pattern"]["quality_contract"] == left["quality_contract"]
    assert report["report"]["status"] == "generalized_draft"
    right["quality_contract"]["pattern_checks"][0]["absolute_tolerance"] = "0.01"
    blocked = generalize([left, right], pattern_id="data.reusable", version="2.0.0")
    assert blocked["pattern"] is None
    assert blocked["report"]["different_contract_fields"] == ["quality_contract"]
    alternative = generalize([left, right], pattern_id="data.reusable", version="2.0.0", variant=0)
    assert alternative["report"]["status"] == "variant_draft"
    assert alternative["pattern"]["quality_contract"] == left["quality_contract"]
    assert right["quality_contract"] != left["quality_contract"]


def test_quality_checks_preserve_numbers_citations_and_exact_decimals():
    fidelity = text_pattern()["quality_contract"]["pattern_checks"][0]
    assert check_reference_quality(
        fidelity, {"text": "值 1.10 [證據]"}, {"text": "另寫 1.10 [證據]"}
    )
    assert not check_reference_quality(
        fidelity, {"text": "值 1.10 [證據]"}, {"text": "另寫 1.1 [證據]"}
    )
    reconcile = data_pattern()["quality_contract"]["pattern_checks"][0]
    rows = [{"id": "B", "amount": "0.10"}, {"id": "B", "amount": "0.20"}]
    assert check_reference_quality(
        reconcile, {"left": rows, "right": rows}, {"status": "invalid_input"}
    )
    assert not check_reference_quality(
        reconcile, {"left": rows, "right": rows}, {"status": "matched"}
    )


def test_generalizer_lifts_configured_profile_values_without_copying_them():
    left, right = text_pattern(), text_pattern()
    right["pattern"]["version"] = "1.1.0"
    left["graph"]["nodes"][0]["parameters"] = {"language": "zh-TW"}
    right["graph"]["nodes"][0]["parameters"] = {"language": "en-GB"}
    schema = load_asset("profile_schema", FIXTURES / "profile_schema.yaml").model
    common = generalize(
        [left, right],
        pattern_id="text.portable",
        version="1.0.0",
        profile=schema.model_dump(mode="json"),
        profile_mappings={"copy/language": "language"},
    )
    pattern = PatternManifest.model_validate(common["pattern"])
    assert pattern.graph.nodes[0].parameters == {}
    assert not any(value in json.dumps(common) for value in ("zh-TW", "en-GB"))
    configured = bind_profile_parameters(
        pattern,
        {schema.ref: schema},
        {schema.ref: {"language": "ja-JP"}},
    )
    assert configured.nodes[0].parameters == {"language": "ja-JP"}
    assert pattern.graph.nodes[0].parameters == {}
    with pytest.raises(ValueError, match="不完整"):
        bind_profile_parameters(pattern, {schema.ref: schema}, {})
