"""Build optional inert Text/Data Pack ZIPs and a separate local data implementation wheel."""

import hashlib
import json
import zipfile

from routinekeel_contracts.asset_loading import load_pack
from routinekeel_contracts.domain_assets import canonical_json

VERSION = "1.0.0"


def _task(name, capability, inputs, outputs):
    return {
        "id": name,
        "kind": "task",
        "capability": capability,
        "capability_version": VERSION,
        "inputs": {key: {"source": "input://" + key, "port": port} for key, port in inputs.items()},
        "outputs": outputs,
        "permissions": [],
        "side_effect": "none",
    }


def _requirement(name, capability, loci, **options):
    return {
        "id": name,
        "capability": capability,
        "version": VERSION,
        "required": True,
        "portability": "flexible",
        "execution_loci": loci,
        **options,
    }


def _definition(capability, kind, inputs, outputs, loci, invariants, quality=None):
    return {
        "id": capability,
        "version": VERSION,
        "kind": kind,
        "inputs": inputs,
        "outputs": outputs,
        "execution_loci": loci,
        "permissions": [],
        "side_effects": "none",
        "invariants": invariants,
        "substitution": {
            "mode": "flexible",
            "requires": ["contract_conformance", "behavioral_qualification"],
        },
        "quality": quality or {},
    }


def _triggers(categories, intents, positive, negative, prerequisites, **options):
    return {
        "design": {
            "activation": "suggest",
            "task_categories": categories,
            "intents": intents,
            "positive_examples": positive,
            "negative_examples": negative,
            "prerequisites": prerequisites,
            **options,
        },
        "runtime": {"activation": "compiled_graph_only"},
        "evolution": {"signals": ["confirmed_pattern_defect"]},
    }


def _pattern(ref, title, inputs, outputs, graph, requirements, triggers, quality):
    return {
        "pattern_schema_version": "1.0",
        "pattern": {
            "id": ref,
            "version": VERSION,
            "kind": "reusable_graph",
            "title": title,
            "declared_domains": [ref.split(".")[0]],
        },
        "boundary": {"inputs": inputs, "outputs": outputs},
        "graph": graph,
        "requirements": requirements,
        "triggers": triggers,
        "quality_contract": quality,
        "tests": {
            "golden_cases_manifest": "cases/index.json",
            "required_edges": ["normal", "negative", "boundary"],
        },
    }


def text_reference():
    text = {"type": "string"}
    inputs, outputs = {"source": text}, {"result": text}
    rewrite = _task("rewrite", "llm.text_rewrite", inputs, outputs)
    review = {
        "id": "review",
        "kind": "human_review",
        "prompt": "請核對數字與引用後確認交付。",
        "inputs": {"draft": {"source": "artifact://rewrite/result", "port": text}},
        "outputs": outputs,
    }
    quality = {
        "pattern_checks": [{"kind": "text_fidelity", "source": "source", "output": "result"}],
        "review_policy": {"required": True, "approved_node": "review"},
    }
    graph = {
        "inputs": inputs,
        "nodes": [rewrite, review],
        "outputs": {"result": "artifact://review/result"},
    }
    pattern = _pattern(
        "text.review_and_humanize",
        "自然改寫與原意校閱",
        inputs,
        outputs,
        graph,
        [_requirement("rewrite", "llm.text_rewrite", ["host_mediated"])],
        _triggers(
            ["reporting", "writing"],
            ["humanize_with_fidelity"],
            ["改寫但保留數字和來源"],
            ["擷取法律原文", "逐字轉錄"],
            ["source_text"],
            privacy="host_mediated",
            excluded_intents=["quote_exact_legal", "verbatim_transcript"],
        ),
        quality,
    )
    contract = _definition(
        "llm.text_rewrite",
        "llm",
        inputs,
        outputs,
        ["host_mediated"],
        ["preserve_numbers", "preserve_citations"],
    )
    records = [
        (
            "notice",
            "positive",
            "normal",
            "計畫將於 15 日施行 [公告 A]",
            "計畫從 15 日開始實施 [公告 A]",
        ),
        ("rates", "positive", "normal", "本季降低 2.50% [期報 2]", "本季減少 2.50% [期報 2]"),
        ("quote", "negative", "negative", "原文「不得變更」 [條文 4]", "原文「不得變更」 [條文 4]"),
        ("blank", "boundary", "boundary", "", ""),
    ]
    cases = [
        (
            name,
            category,
            edge,
            {"source": before},
            {"result": after},
            [
                {"step": "rewrite", "kind": "host", "outputs": {"result": after}},
                {"step": "review", "kind": "human", "outputs": {"result": after}},
            ],
        )
        for name, category, edge, before, after in records
    ]
    return pattern, [contract], cases


def data_reference():
    rows = {"type": "array", "items": {"type": "object"}}
    status = {"type": "string"}
    inputs, outputs = {"left": rows, "right": rows}, {"status": status}
    node = _task("compare", "data.reconcile", inputs, outputs)
    check = {
        "kind": "decimal_reconciliation",
        "left": "left",
        "right": "right",
        "key": "id",
        "amount": "amount",
        "absolute_tolerance": "0",
        "output": "status",
    }
    graph = {"inputs": inputs, "nodes": [node], "outputs": {"status": "artifact://compare/status"}}
    pattern = _pattern(
        "data.pre_analysis_reconcile",
        "分析前核對資料",
        inputs,
        outputs,
        graph,
        [
            _requirement(
                "reconcile",
                "data.reconcile",
                ["local_runtime", "host_mediated"],
                constraints={"numeric_representation": "decimal"},
            )
        ],
        _triggers(
            ["analytics", "reporting"],
            ["validate_aggregates"],
            ["核對明細與彙總"],
            ["只複製一個 CSV"],
            ["two_comparable_datasets", "explicit_match_key_or_rule"],
            gate="reconcile",
        ),
        {"pattern_checks": [check], "delivery_gate": {"port": "status", "equals": "matched"}},
    )
    definitions = [
        _definition(
            "data.reconcile",
            "deterministic_validation",
            inputs,
            outputs,
            ["local_runtime", "host_mediated"],
            ["input_data_immutable", "no_silent_row_drop"],
            {"numeric_representation": "decimal"},
        ),
        _definition(
            "tabular.read",
            "deterministic",
            {"source": {"type": "string"}},
            {"rows": rows},
            ["local_runtime", "host_mediated"],
            ["preserve_source_values"],
        ),
    ]
    records = [
        ("invoice", "positive", "normal", "A", "127.50", "127.500", "matched"),
        ("rate", "positive", "normal", "B", "0.10", "0.100", "matched"),
        ("variance", "negative", "negative", "A", "1.01", "1.00", "mismatched"),
        ("missing", "boundary", "boundary", "B", "1.0", None, "invalid_input"),
    ]
    cases = [
        (
            name,
            category,
            edge,
            {
                "left": [{"id": key, "amount": left}],
                "right": [{"id": key, "amount": right}] if right is not None else [],
            },
            {"status": value},
            [{"step": "compare", "kind": "host", "outputs": {"status": value}}],
        )
        for name, category, edge, key, left, right, value in records
    ]
    return pattern, definitions, cases


def write_reference_pack(path, name, package, *, pack_version=VERSION):
    pattern, capabilities, records = package
    files = {
        "patterns/reference.json": canonical_json(pattern),
        **{f"capabilities/{cap['id']}.json": canonical_json(cap) for cap in capabilities},
    }
    cases = []
    for case_id, category, edge, inputs, expected, replies in records:
        origin = {"kind": "synthetic"}
        if category == "positive":
            filename = f"cases/{case_id}.json"
            data = canonical_json({"inputs": inputs, "expected": expected})
            files[filename] = data
            origin = {
                "kind": "business",
                "path": filename,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        cases.append(
            {
                "id": case_id,
                "class": category,
                "edge": edge,
                "inputs": inputs,
                "expected": expected,
                "origin": origin,
                "replies": replies,
            }
        )
    files["cases/index.json"] = canonical_json({"case_schema_version": "1.0", "cases": cases})
    manifest = {
        "pack_schema_version": "1.0",
        "pack": {
            "id": f"routinekeel.{name}",
            "version": pack_version,
            "title": f"Reference {name.title()} Pack",
            "domains": [name],
            "publisher": "RoutineKeel",
            "runtime_compatibility": ">=0.3,<0.4",
        },
        "capability_refs": [f"{cap['id']}@{cap['version']}" for cap in capabilities],
        "pattern_refs": [f"{pattern['pattern']['id']}@{pattern['pattern']['version']}"],
        "provenance": {"source": "RoutineKeel authored reference cases", "license": "MIT"},
        "assets": {
            filename: hashlib.sha256(data).hexdigest() for filename, data in sorted(files.items())
        },
    }
    with zipfile.ZipFile(path, "w") as archive:
        for filename, data in sorted({"pack.json": canonical_json(manifest), **files}.items()):
            info = zipfile.ZipInfo(filename)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
    load_pack(path)
    return path


def build_reference_packs(root, destination, run):
    destination.mkdir()
    (destination / "README.md").write_bytes((root / "docs/specs/domain-packs.md").read_bytes())
    packs = [
        write_reference_pack(destination / f"RoutineKeel-Pack-{name}-{VERSION}.zip", name, package)
        for name, package in (("text", text_reference()), ("data", data_reference()))
    ]
    run(
        ["uv", "build", "--wheel", "--out-dir", str(destination)],
        cwd=root / "capability_packs/data_adapter",
    )
    wheels = sorted(destination.glob("routinekeel_data_adapter-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("Expected exactly one optional Data Adapter wheel")
    records = [
        {"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in [*packs, *wheels]
    ]
    (destination / "SHA256SUMS").write_text(
        "".join(f"{item['sha256']}  {item['file']}\n" for item in records), encoding="utf-8"
    )
    (destination / "versions.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )
    return records
