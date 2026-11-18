import json
import os
import shutil
import subprocess

import pytest
from compiler_helpers import ROOT, build_ir, runtime_python
from ir_cases import valid_ir
from workflow_studio.registry.policy import ToolPolicy

pytest.importorskip("PIL", reason="LLM Runtime fixtures require Studio source dependencies")


def llm_ir(budget=100_000):
    payload = valid_ir()
    payload["secrets"] = ["provider_key"]
    node = payload["nodes"][0]
    node.update(
        kind="llm",
        tool="llm.structured",
        side_effects="external",
        cache_policy={"mode": "none", "fingerprint_fields": []},
        resources={"memory_mb": 64, "disk_mb": 10, "network": True},
        llm_policy={
            "model": "fixture-model",
            "api_key": "secret://provider_key",
            "max_calls": 1,
            "max_output_tokens": 128,
            "budget_micro_usd": budget,
            "timeout_seconds": 10,
        },
        parameters={
            "endpoint": "https://example.invalid/v1/chat/completions",
            "prompt": "描述輸入",
            "output_fields": {"summary": "string"},
            "input_usd_per_million": 1.0,
            "output_usd_per_million": 1.0,
            "pricing_note": "Synthetic test rates, not actual pricing",
        },
    )
    node["outputs"]["result"] = {"type": "file", "format": "json", "path": "answer.json"}
    return payload


def run_fixture(tmp_path, *, scenario="success", authorized=True, budget=100_000):
    policy = ToolPolicy(
        "windows" if os.name == "nt" else "linux",
        frozenset({"workspace.write", "model.call", "secret.read"}),
        True,
        True,
        100_000,
    )
    studio, p, revision, policy = build_ir(tmp_path, llm_ir(budget), policy)
    build = studio.compile(p.project_id, revision.revision_id, policy)
    workspace = tmp_path / "runtime"
    workspace.mkdir()
    shutil.copytree(build["path"] / "package", workspace / "generated")
    (workspace / "input.txt").write_text("fixture input", encoding="utf-8")
    script = """
import io,json,sys,urllib.request
from pathlib import Path
from flow_base.core.service import WorkflowService
from flow_base.core.types import StartRequest
from flow_base.runtime import FlowError
root,scenario=Path(sys.argv[1]),sys.argv[2]
calls=[]
class FakeOpener:
    def open(self,request,timeout):
        calls.append(json.loads(request.data))
        text='{"summary":"streamed result"}' if scenario!='bad_type' else '{"summary":12}'
        chunks=[{'choices':[{'delta':{'content':text[:9]}}]}, {'choices':[{'delta':{'content':text[9:]},'finish_reason':'stop'}]},
                {'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':20,'prompt_tokens_details':{'cached_tokens':2}}}]
        if scenario=='interrupted':chunks=chunks[:1]
        data=b''.join(b'data: '+json.dumps(c).encode()+b'\\n\\n' for c in chunks)
        if scenario!='interrupted':data+=b'data: [DONE]\\n\\n'
        return io.BytesIO(data)
urllib.request.build_opener=lambda *args:FakeOpener()
service=WorkflowService(root)
try:
    result=service.run_workflow_sync(StartRequest(task='generated',source=root/'input.txt',output=root/'output',offline=False))
    answer={'ok':True,'usage':result['token_usage'],'calls':len(calls),'run_id':result['run_id']}
except FlowError as exc:
    answer={'ok':False,'code':exc.code,'calls':len(calls)}
print('FIXTURE_RESULT='+json.dumps(answer))
"""
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() in {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "HOME"}
    }
    env.update(
        PYTHONPATH=str(ROOT / "routinekeel/flow_base/src"),
        PYTHONIOENCODING="utf-8",
        WORKFLOW_STUDIO_SECRET_PROVIDER_KEY="fixture-key",
    )
    if authorized:
        env["WORKFLOW_STUDIO_LLM_AUTHORIZATION"] = build["mapping"]["ir_hash"]
    child = subprocess.run(
        [runtime_python(), "-c", script, str(workspace), scenario],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert child.returncode == 0, child.stdout + child.stderr
    result = json.loads(
        next(
            line.removeprefix("FIXTURE_RESULT=")
            for line in child.stdout.splitlines()
            if line.startswith("FIXTURE_RESULT=")
        )
    )
    studio.close()
    return result, child.stdout + child.stderr, workspace


def test_runtime_llm_streams_validates_and_tracks_usage(tmp_path):
    result, console, workspace = run_fixture(tmp_path)
    assert result["ok"] is True, result
    assert result["calls"] == 1
    assert result["usage"]["registered_requests"] == 1
    assert result["usage"]["input_total"] == 10
    assert result["usage"]["input_cached"] == 2
    assert "streamed result" in console
    assert json.loads((workspace / "output/artifacts/result/answer.json").read_text()) == {
        "summary": "streamed result"
    }


@pytest.mark.parametrize("scenario", ["bad_type", "interrupted"])
def test_runtime_llm_invalid_or_partial_output_is_not_published(tmp_path, scenario):
    result, _, workspace = run_fixture(tmp_path, scenario=scenario)
    assert result["ok"] is False
    assert result["calls"] == 1
    assert not (workspace / "output/artifacts/result/answer.json").exists()


@pytest.mark.parametrize(
    "authorized,budget,code", [(False, 100_000, "PREFLIGHT_BLOCKED"), (True, 1, "BUDGET_EXHAUSTED")]
)
def test_runtime_authorization_and_budget_prevent_network_call(tmp_path, authorized, budget, code):
    result, _, _ = run_fixture(tmp_path, authorized=authorized, budget=budget)
    assert result == {"ok": False, "code": code, "calls": 0}
