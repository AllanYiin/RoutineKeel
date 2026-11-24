import hashlib
import sys
import zipfile

import pytest
from flow_base.core.service import WorkflowService
from flow_base.runtime import FlowError
from flow_base.studio import studio_request
from routinekeel_contracts.domain_assets import canonical_json, content_hash
from test_pattern_qualification import data_pattern, text_pattern
from test_pattern_registry import decision
from workflow_studio.contracts import StudioError
from workflow_studio.patterns.trigger import TaskContext, inspect, measure, rank
from workflow_studio.service import StudioService

TEXT = "text.preserve@1.0.0"
DATA = "data.compare@1.0.0"
TABLE = {"type": "array", "schema_id": "tabular.dataset.v1"}


def candidates():
    text, data = text_pattern(), data_pattern()
    text["triggers"]["design"].update(
        task_categories=["transcript", "reporting"],
        intents=["preserve_source"],
        positive_examples=["保留原文"],
        negative_examples=["擷取法律原文", "只複製檔案"],
        prerequisites=["source_text"],
        excluded_intents=["quote_exact_legal"],
    )
    data["triggers"]["design"].update(
        task_categories=["analytics", "reporting"],
        intents=["validate_aggregates"],
        positive_examples=["核對明細與彙總"],
        negative_examples=["只複製檔案", "沒有比較對象"],
        prerequisites=["two_comparable_datasets", "explicit_match_key_or_rule"],
        gate="reconcile",
    )
    data["boundary"]["inputs"] = {name: dict(TABLE) for name in ("left", "right")}
    return text, data


def activate(studio, path, patterns):
    registry = studio.tools.packs
    entries = {
        f"patterns/{index}.json": canonical_json(pattern) for index, pattern in enumerate(patterns)
    }
    entries["cases/index.json"] = b"{}"
    catalog = {
        "pack_schema_version": "1.0",
        "pack": {
            "id": "routinekeel.triggers",
            "version": "1.0.0",
            "title": "Triggers",
            "domains": ["text", "data"],
            "publisher": "local",
            "runtime_compatibility": ">=0.3,<0.4",
        },
        "pattern_refs": [
            f"{pattern['pattern']['id']}@{pattern['pattern']['version']}" for pattern in patterns
        ],
        "provenance": {"source": "isolated recommendation test", "license": "MIT"},
        "assets": {name: hashlib.sha256(body).hexdigest() for name, body in entries.items()},
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in {"pack.json": canonical_json(catalog), **entries}.items():
            archive.writestr(name, body)
    pack = registry.decide(decision("approve", registry.register(path)))
    for ref in pack["manifest"]["pattern_refs"]:
        info = registry.pattern_status(ref)
        # Registration and approval run through real Registry APIs. The trigger
        # benchmark isolates ranking with a fixture receipt from the verifier boundary.
        report = {
            "pattern_ref": ref,
            "pattern_hash": info["content_hash"],
            "pack_ref": pack["ref"],
            "pack_hash": pack["manifest_hash"],
            "verdict": "PASS",
            "conformance_report": {"builder_receipt": {"simulated": True}},
            "quality_report": {"verdict": "PASS"},
        }
        receipt = content_hash(report)
        studio.repository.connection.execute(
            "INSERT INTO studio_pattern_trials VALUES (?,?,?,?,'PASS')",
            (ref, info["content_hash"], receipt, canonical_json(report)),
        )
        registry.decide_pattern(decision("approve", info, evidence=receipt))


def compare_task(text="核對明細與彙總", **overrides):
    return {
        "intent": text,
        "category": "analytics",
        "intents": ["validate_aggregates"],
        "available_inputs": {"line_items": TABLE, "summary": TABLE},
        "match_keys": ["id"],
        **overrides,
    }


def text_task(text="保留原文並整理交付", **overrides):
    return {
        "intent": text,
        "category": "transcript",
        "intents": ["preserve_source"],
        "available_inputs": {"text": {"type": "string"}},
        "facts": ["source_text"],
        **overrides,
    }


def test_rank_40_positive_and_negative_cases_progressively(tmp_path, monkeypatch):
    positive_text = [
        "保留版本號",
        "保持逐字稿",
        "保留原文",
        "確認數字",
        "沿用報告文字",
        "引用不改",
        "保持內容",
        "檢查原稿",
        "逐字交付",
        "保全唯一稿",
    ]
    positive_data = [
        "核對明細與彙總",
        "比對兩份財務資料",
        "核實每列報表",
        "核對總價",
        "檢查數字一致",
        "比較基準與當期",
        "找出有金額誤差的列",
        "以編號比較表格",
        "檢查同一筆資料",
        "驗證小計與來源",
    ]
    negatives = [
        "替我整理這份報表",
        "找出檔案欄位",
        "只複製檔案",
        "沒有比較對象",
        "輸出資料字典",
        "用 CSV 產生圖表",
        "檢查單一來源",
        "刪除多餘欄位",
        "列印現有摘要",
        "保留收入明細",
    ]
    examples = [
        *((text_task(text), [TEXT]) for text in positive_text),
        *((compare_task(text), [DATA]) for text in positive_data),
        *((compare_task(text, available_inputs={"only": TABLE}), []) for text in negatives),
        *((text_task(text, intents=["quote_exact_legal"]), []) for text in negatives),
    ]
    with StudioService.open_workspace(tmp_path / "design") as studio:
        activate(studio, tmp_path / "registry.zip", candidates())
        registry = studio.tools.packs
        read = registry.get_asset

        def check_no_graph(kind, ref):
            assert kind != "pattern", "L1/L2 ranking loaded the complete Graph"
            return read(kind, ref)

        monkeypatch.setattr(registry, "get_asset", check_no_graph)
        stats = measure(registry, examples)
        assert stats == {
            "cases": 40,
            "top_k": 3,
            "precision_at_k": 1.0,
            "recall_at_k": 1.0,
            "false_trigger_rate": 0,
            "repeated_rejection_rate": 0,
            "model_tokens": 0,
        }
        different = compare_task(
            available_inputs={
                "source": TABLE,
                "other": {"type": "array", "schema_id": "tabular.incompatible"},
            }
        )
        assert not rank(registry, TaskContext.model_validate(different), {})["recommendations"]
        no_key = rank(registry, TaskContext.model_validate(compare_task(match_keys=[])), {})
        assert no_key["recommendations"][0]["missing_inputs"] == ["explicit_match_key_or_rule"]
        assert no_key["usage"]["graph_reads"] == 0


def test_recommendations_do_not_loop_or_duplicate_same_gate(tmp_path):
    _, favored = candidates()
    from copy import deepcopy

    duplicate, left, right = (deepcopy(favored) for _ in range(3))
    duplicate["pattern"]["id"] = "data.alternative"
    duplicate["triggers"]["design"]["priority"] = 10
    left["pattern"]["id"] = "data.loop_left"
    left["triggers"]["design"]["suggests"] = ["data.loop_right@1.0.0"]
    right["pattern"]["id"] = "data.loop_right"
    right["triggers"]["design"]["suggests"] = ["data.loop_left@1.0.0"]
    with StudioService.open_workspace(tmp_path / "design") as studio:
        activate(studio, tmp_path / "registry.zip", [favored, duplicate, left, right])
        result = rank(studio.tools.packs, TaskContext.model_validate(compare_task()), {})
        assert [item["ref"] for item in result["recommendations"]] == [DATA]
        previous = {DATA: {"decision": "accepted"}}
        assert (
            rank(studio.tools.packs, TaskContext.model_validate(compare_task()), previous)[
                "recommendations"
            ]
            == []
        )


def test_text_host_decisions_persist_without_install_and_cannot_apply_stale_choices(
    tmp_path, monkeypatch
):
    with StudioService.open_workspace(tmp_path) as studio:
        activate(studio, tmp_path / "registry.zip", candidates())
        with pytest.raises(StudioError, match="先在草稿"):
            inspect(studio, DATA, "L3")
    monkeypatch.setenv("ROUTINEKEEL_STUDIO_PYTHON", sys.executable)
    project = studio_request(tmp_path, "create", {"name": "勾稽訪談", "task": "核對報表"})
    pid = project["project_id"]
    params = {
        "project_id": pid,
        "expected_revision": project["revision"],
        "request_id": "suggest",
        "context": compare_task(),
    }
    suggestion = studio_request(tmp_path, "recommend_patterns", params)
    assert studio_request(tmp_path, "recommend_patterns", params) == suggestion
    assert suggestion["usage"]["graph_reads"] == 0
    (row,) = suggestion["recommendations"]
    assert "graph" not in studio_request(
        tmp_path, "inspect_pattern", {"pattern_ref": DATA, "level": "L2"}
    )
    scope = {
        "project_id": pid,
        "expected_revision": suggestion["revision"],
        "request_id": "reply",
        "recommendation_id": row["recommendation_id"],
    }
    with pytest.raises(FlowError, match="不一致"):
        studio_request(
            tmp_path, "decide_pattern", {**scope, "decision": "accepted", "user_text": "不要"}
        )
    result = studio_request(
        tmp_path,
        "decide_pattern",
        {
            **scope,
            "decision": "accepted",
            "user_text": "採用這項勾稽建議",
            "message_ref": "test/turn2",
        },
    )
    assert result["decisions"][DATA]["message_ref"] == "test/turn2"
    loaded = studio_request(
        tmp_path,
        "inspect_pattern",
        {
            "pattern_ref": DATA,
            "level": "L3",
            "project_id": pid,
        },
    )
    assert loaded["pattern"]["graph"]["nodes"][0]["id"] == "compare"
    with pytest.raises(FlowError):
        studio_request(
            tmp_path,
            "decide_pattern",
            {
                **scope,
                "request_id": "stale",
                "decision": "rejected",
                "user_text": "不要",
            },
        )
    toggle = studio_request(
        tmp_path,
        "configure_patterns",
        {
            "project_id": pid,
            "expected_revision": result["revision"],
            "request_id": "disable",
            "enabled": False,
        },
    )
    empty = studio_request(
        tmp_path,
        "recommend_patterns",
        {
            **params,
            "expected_revision": toggle["revision"],
            "request_id": "paused-discovery",
        },
    )
    assert empty["recommendations"] == []
    assert not WorkflowService(tmp_path).workspace.discover(read_only=True)
