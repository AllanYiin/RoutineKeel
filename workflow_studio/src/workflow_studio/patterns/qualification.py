"""Qualify Pattern versions with isolated Builder evidence and explicit test provenance."""

import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue, ValidationError
from routinekeel_contracts.asset_loading import load_data_document
from routinekeel_contracts.domain_assets import (
    AssetRef,
    Fingerprint,
    PackManifest,
    canonical_json,
    content_hash,
)
from routinekeel_contracts.models import Contract, Identifier
from routinekeel_contracts.privacy import contains_private_data

from ..contracts import StudioError
from .quality import check_reference_quality


class CaseOrigin(Contract):
    kind: Literal["business", "synthetic"]
    path: str | None = None
    sha256: Fingerprint | None = None


class PatternCase(Contract):
    id: Identifier
    class_: Literal["positive", "negative", "boundary"] = Field(alias="class")
    edge: Identifier
    inputs: dict[Identifier, JsonValue]
    expected: dict[Identifier, JsonValue] = Field(min_length=1)
    origin: CaseOrigin
    replies: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=40)
    expected_result_status: (
        Literal["accepted", "processed", "review_required", "rejected"] | None
    ) = None


class PatternSuite(Contract):
    case_schema_version: Literal["1.0"]
    cases: list[PatternCase] = Field(min_length=4, max_length=20)
    profile_fixture_values: dict[AssetRef, dict[Identifier, JsonValue]] = Field(
        default_factory=dict
    )


def _validate_quality_rules(pattern):
    fields = {
        "text_fidelity": {"kind", "source", "output"},
        "decimal_reconciliation": {
            "kind",
            "left",
            "right",
            "key",
            "amount",
            "absolute_tolerance",
            "output",
        },
    }
    rules = pattern.quality_contract.get("pattern_checks", [])
    if not isinstance(rules, list):
        raise StudioError("TEST_FAILED", "Pattern 品質檢查宣告不受支援。")
    for rule in rules:
        kind = rule.get("kind") if isinstance(rule, dict) else None
        if (
            not isinstance(kind, str)
            or kind not in fields
            or rule.keys() != fields[kind]
            or not all(isinstance(value, str) for value in rule.values())
            or rule["output"] not in pattern.graph.outputs
        ):
            raise StudioError("TEST_FAILED", "Pattern 品質檢查欄位或輸出埠不符。")
        inputs = [rule["source"]] if kind == "text_fidelity" else [rule["left"], rule["right"]]
        if not set(inputs) <= pattern.graph.inputs.keys():
            raise StudioError("TEST_FAILED", "Pattern 品質檢查引用未知的輸入埠。")
    return rules


def _read_suite(archive, pack, pattern):
    try:
        path = pattern.tests.golden_cases_manifest
        suite = PatternSuite.model_validate(load_data_document(archive.read(path), path))
        if contains_private_data(suite.model_dump(mode="json", by_alias=True)):
            raise StudioError("SECRET_IN_PORTABLE_PACKAGE", "Pattern 測例含未清除的私密資料。")
        cases = suite.cases
        if (
            len({case.id for case in cases}) != len(cases)
            or {case.class_ for case in cases} != {"positive", "negative", "boundary"}
            or not set(pattern.tests.required_edges) <= {case.edge for case in cases}
        ):
            raise StudioError("TRIAL_CASES_REQUIRED", "案例識別、正反例或必要邊界覆蓋不足。")
        for case in cases:
            if case.expected.keys() != pattern.graph.outputs.keys():
                raise StudioError("TRIAL_CASES_REQUIRED", "案例須涵蓋所有具名輸出。")
            if case.origin.kind == "business":
                path = case.origin.path
                if not path or pack.assets.get(path) != case.origin.sha256:
                    raise StudioError("TRIAL_CASES_REQUIRED", "業務案例須綁定 ZIP 內的來源指紋。")
                source = load_data_document(archive.read(path), path)
                if source != {"inputs": case.inputs, "expected": case.expected}:
                    raise StudioError("TRIAL_CASES_REQUIRED", "案例輸入或預期成果與來源不一致。")
        business = [
            case for case in cases if case.class_ == "positive" and case.origin.kind == "business"
        ]
        if len({content_hash(case.inputs) for case in business}) < 2:
            raise StudioError("TRIAL_CASES_REQUIRED", "至少需要兩組不同的正向業務案例。")
    except (ValidationError, KeyError, zipfile.BadZipFile) as exc:
        raise StudioError("TRIAL_CASES_REQUIRED", "測例清單或其來源格式無效。") from exc
    return suite


def _execute(runtime_python, pattern, cases, profile_schemas, profile_values):
    args = [
        str(Path(runtime_python or sys.executable).absolute()),
        "-I",
        "-m",
        "flow_base.graph.pattern_trial",
    ]
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "SYSTEMDRIVE",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "LOCALAPPDATA",
            "APPDATA",
            "LANG",
            "LC_ALL",
            "HOME",
        }
    }
    try:
        result = subprocess.run(
            args,
            input=canonical_json(
                {
                    "pattern": pattern,
                    "cases": cases,
                    "profile_schemas": profile_schemas,
                    "profile_values": profile_values,
                }
            ),
            capture_output=True,
            env=env,
            timeout=340,
            check=False,
        )
        if result.returncode:
            return {"verdict": "NOT_RUN", "code": "PATTERN_TRIAL_RUNTIME_UNAVAILABLE"}
        evidence = json.loads(result.stdout)
        if evidence["verdict"] not in {"PASS", "FAIL", "NOT_RUN"}:
            raise ValueError("Unknown verdict")
        return evidence
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return {"verdict": "NOT_RUN", "code": "PATTERN_TRIAL_RUNTIME_UNAVAILABLE"}


def _report(suite, runtime, rules):
    if runtime["verdict"] != "PASS":
        return {
            "verdict": runtime["verdict"],
            "code": runtime.get("code", "TEST_FAILED"),
            "cases": [],
            "provider_calls": 0,
        }
    evidence = runtime["results"]
    if len(evidence) != len(suite.cases) or runtime["provider_calls"] != 0:
        raise StudioError("TEST_FAILED", "試做回傳的案例數量或工具用量不符。")
    results = []
    for case, observed in zip(suite.cases, evidence, strict=True):
        checks = []
        for index, rule in enumerate(rules, 1):
            passed = check_reference_quality(rule, case.inputs, observed["outputs"])
            checks.append({"check": index, "status": "PASS" if passed else "FAIL"})
        simulated = observed["simulated_host_and_review"]
        results.append(
            {
                "id": case.id,
                "class": case.class_,
                "edge": case.edge,
                "source_case_provenance": case.origin.model_dump(mode="json"),
                "case_hash": content_hash(case.model_dump(mode="json", by_alias=True)),
                "run_id": observed["run_id"],
                "outputs_hash": observed["outputs_hash"],
                "external_behavior": "simulated" if simulated else "local_execution",
                "simulated_steps": simulated,
                "quality_checks": checks,
            }
        )
    pass_quality = all(
        check["status"] == "PASS" for case in results for check in case["quality_checks"]
    )
    return {
        "verdict": "PASS" if pass_quality else "FAIL",
        "conformance_report": {
            "builder_receipt": runtime["builder_receipt"],
            "candidate_hash": runtime["candidate_hash"],
            "missing_fixture_preflight": runtime["missing_fixture_preflight"],
        },
        "quality_report": {
            "verdict": "PASS" if pass_quality else "FAIL",
            "human_review_required": any(case["simulated_steps"] for case in results),
        },
        "provider_calls": 0,
        "cases": results,
    }


def qualify(studio, pack_ref, pattern_ref, *, runtime_python=None):
    registry = studio.tools.packs
    item = registry.get(pack_ref)
    if item["status"] != "active" or pack_ref not in registry.pattern_pack_refs(pattern_ref):
        raise StudioError(
            "PATTERN_APPROVAL_REQUIRED", "試做需要已核准的 Pack 與其中的 Pattern 版本。"
        )
    asset = registry.get_asset("pattern", pattern_ref)
    module = asset.model
    with zipfile.ZipFile(io.BytesIO(registry.archive_bytes(pack_ref))) as archive:
        suite = _read_suite(archive, PackManifest.model_validate(item["manifest"]), module)
    rules = _validate_quality_rules(module)
    cases = [
        case.model_dump(
            include={"inputs", "expected", "replies", "expected_result_status"},
            mode="json",
            exclude_none=True,
        )
        for case in suite.cases
    ]
    profile_schemas = {
        ref: registry.get_asset("profile_schema", ref).model.model_dump(mode="json")
        for ref in {parameter.profile_schema for parameter in module.profile_parameters}
    }
    runtime = _execute(
        runtime_python,
        module.model_dump(mode="json"),
        cases,
        profile_schemas,
        suite.profile_fixture_values,
    )
    report = {
        **_report(suite, runtime, rules),
        "pattern_ref": pattern_ref,
        "pattern_hash": asset.sha256,
        "pack_ref": pack_ref,
        "pack_hash": item["manifest_hash"],
        "suite_hash": content_hash(suite.model_dump(mode="json", by_alias=True)),
    }
    fingerprint = content_hash(report)
    if report["verdict"] in {"PASS", "FAIL"}:
        with studio.repository.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO studio_pattern_trials VALUES (?,?,?,?,?)",
                (pattern_ref, asset.sha256, fingerprint, canonical_json(report), report["verdict"]),
            )
    return {**report, "qualification_hash": fingerprint}
