import copy
import hashlib
import json
import math
import statistics
import time

from gate_evidence import write_evidence
from ir_cases import tools, valid_ir
from workflow_studio.ir.validator import validate_ir
from workflow_studio.service import StudioService


def twenty_node_ir():
    payload = valid_ir()
    payload["nodes"] = []
    for index in range(20):
        node = copy.deepcopy(valid_ir()["nodes"][0])
        node["id"] = f"step{index}"
        node["depends_on"] = [] if index == 0 else [f"step{index - 1}"]
        node["inputs"]["source"]["source"] = (
            "input://source" if index == 0 else f"artifact://step{index - 1}/result"
        )
        node["outputs"]["result"]["path"] = f"artifacts/step{index}.txt"
        node["postconditions"][0]["reference"] = f"artifact://step{index}/result"
        payload["nodes"].append(node)
    payload["outputs"] = {"result": "artifact://step19/result"}
    return payload


def test_twenty_node_static_validation_p95_below_500_ms(tmp_path):
    payload, signatures = twenty_node_ir(), tools()
    source_versions = {("source_fixture", 1)}
    for _ in range(10):
        validate_ir(payload, signatures, source_versions=source_versions)
    durations = []
    for _ in range(200):
        start = time.perf_counter_ns()
        validated = validate_ir(payload, signatures, source_versions=source_versions)
        durations.append((time.perf_counter_ns() - start) / 1_000_000)
        assert len(validated.nodes) == 20
    p95 = sorted(durations)[math.ceil(len(durations) * 0.95) - 1]
    write_evidence(
        tmp_path,
        "static-20-nodes",
        {
            "dataset_sha256": hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode()
            ).hexdigest(),
            "nodes": 20,
            "source_versions_checked": 1,
            "iterations": 200,
            "warmups": 10,
            "p50_ms": statistics.median(durations),
            "p95_ms": p95,
            "max_ms": max(durations),
            "threshold_ms": 500,
            "passed": p95 < 500,
            "includes_file_io_or_model": False,
        },
    )
    assert p95 < 500


def test_hundred_source_listing_and_metadata_do_not_call_model(tmp_path, monkeypatch):
    calls = []

    def forbidden(*args, **kwargs):
        calls.append("model")
        raise AssertionError("Source inventory must not call a model")

    monkeypatch.setattr("workflow_studio.llm.provider.ChatCompletionsProvider.stream", forbidden)
    monkeypatch.setattr("urllib.request.OpenerDirector.open", forbidden)
    root = tmp_path / "fixed-sources"
    root.mkdir()
    corpus = hashlib.sha256()
    for index in range(100):
        data = f"source-{index:03d}\n固定測試資料 {index * 17}\n".encode()
        corpus.update(data)
        (root / f"source-{index:03d}.txt").write_bytes(data)
    with StudioService.open_workspace(tmp_path / "studio") as studio:
        project = studio.projects.create("100 source inventory")
        start = time.perf_counter()
        source = studio.sources.import_path(project.project_id, project.version, root)
        imported_ms = (time.perf_counter() - start) * 1000
        start = time.perf_counter()
        listing = studio.sources.list(project.project_id)
        records = listing[0].manifest["files"]
        elapsed_ms = (time.perf_counter() - start) * 1000
        assert len(records) == 100 and source.source_id == listing[0].source_id
        for index, record in enumerate(records):
            raw = (root / record["path"]).read_bytes()
            assert record["sha256"] == hashlib.sha256(raw).hexdigest()
            assert record["size"] == len(raw) and record["preview"]["kind"] == "text"
        registered = studio.repository.connection.execute(
            "SELECT count(*) FROM studio_llm_calls"
        ).fetchone()[0]
        assert registered == 0 and not calls
    write_evidence(
        tmp_path,
        "sources-100",
        {
            "files": 100,
            "corpus_sha256": corpus.hexdigest(),
            "import_ms": imported_ms,
            "list_and_metadata_ms": elapsed_ms,
            "model_calls": len(calls),
            "registered_calls": registered,
            "passed": True,
        },
    )
