import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for project in [ROOT / 'flow_base', *(ROOT / 'workflows').iterdir()]:
    if (project / 'src').is_dir():
        sys.path.insert(0, str(project / 'src'))


@pytest.fixture
def workflow_design_review(tmp_path):
    """Complete design answers for local fixture graphs, including a mocked research record."""
    def make(proposal, *, workspace=tmp_path):
        draft = proposal["draft_design"]
        step, output = next(
            (path, name)
            for path, row in draft["steps"].items() if row["kind"] == "task"
            for name, port in row["outputs"].items() if port["type"] in {"string", "array", "object"}
        )
        variables = {key: {"kind": row["suggested_kind"]} for key, row in draft["variables"].items()}

        source_url = "https://docs.python.org/3/library/stdtypes.html"
        date = datetime.now(UTC).date().isoformat()
        values = ", ".join(str(request["value"]) for request in draft["research_requests"])
        excerpt = f"Fixture recommendations use 0, 2, or 0.9. Case values include {values}."
        fingerprint = hashlib.sha256(excerpt.encode()).hexdigest()
        store_key = hashlib.sha256(f"{source_url}\n{fingerprint}\n{date}".encode()).hexdigest()
        base = workspace / "flow_base" if (workspace / "workflows").is_dir() else workspace
        store = base / "share_states" / "workflow_studio" / "research_sources"
        store.mkdir(parents=True, exist_ok=True)
        (store / f"{store_key}.json").write_text(json.dumps({
            "url": source_url, "accessed_on": date, "source_sha256": fingerprint, "text": excerpt,
        }), encoding="utf-8")

        def research(value):
            return {
                "query": "fixture query", "url": source_url,
                "title": "Fixture research", "accessed_on": date,
                "applicability": "Fixture tools only", "rationale": "Test recorded source data",
                "recommended_value": value, "excerpt": excerpt, "source_sha256": fingerprint,
            }

        for request in draft["research_requests"]:
            variables[request["variable"]]["research"] = research(request["value"])
        variables[f"check/{step}/format/min"] = {"kind": "fixed", "research": research(0)}
        return {
            "policy": {"mode": "deliver_then_refine", "scope": "fixture task", "critical_steps": {
                step: {"machine": [{"id": "format", "output": output, "evaluator": "length", "min": 0}]},
            }},
            "skills": {name: [step] for name in proposal["skill_sources"]},
            "tool_locations": {path: "local_runtime" for path in draft["tools"]},
            "glossary": {},
            "variables": variables,
            "step_decisions": {path: {
                "purpose": f"Perform the {path} fixture step",
                "completion": "Produce the declared fixture results",
                "critical": path == step,
                "rationale": "This fixture checks the selected transformation",
            } for path in draft["steps"]},
        }
    return make


@pytest.fixture
def desktop_test_catalog(tmp_path_factory, monkeypatch):
    """Emulate a host-published observation outside the workflow's data workspace."""
    directory=tmp_path_factory.mktemp("host-inventory")
    path=directory/"catalog.json"
    record={"tool":"test-sink","version":"1","receipt_id":"test-1","observed":"sent",
            "platforms":[sys.platform],"available":True,"allowed":True,"network":False,
            "permissions":["send"],"side_effect":"external","resources":{"cost_micro_usd":0},
            "input_schema":{"type":"object","required":["body"]},
            "output_schema":{"type":"object","required":["receipt"]}}
    data={"version":1,"expires_at":(datetime.now(UTC)+timedelta(hours=1)).isoformat(),
          "tools":{"test-sink":record}}
    path.write_text(json.dumps(data),encoding="utf-8")
    monkeypatch.setenv("ROUTINEKEEL_HOST_CATALOG",str(path))
    return path


@pytest.fixture
def workspace(tmp_path):
    import shutil

    from package_paths import WORKSPACE

    for task, folder in {'ai_flow01': '課程影片產生字幕', 'ai_flow02': '檔案複製範例'}.items():
        shutil.copytree(WORKSPACE / 'workflows' / ('FLOW_' + folder), tmp_path / task)
    return tmp_path
