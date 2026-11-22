import json
import os

import pytest
from test_llm_runtime import llm_ir
from test_verifier import verification_case
from workflow_studio.compiler.package import digest
from workflow_studio.contracts import StudioError
from workflow_studio.registry.policy import ToolPolicy
from workflow_studio.verifier.authorization import (
    RuntimeAuthorization,
    disclosure,
    environment,
)


def test_verification_requires_exact_runtime_scope_before_credentials(tmp_path, monkeypatch):
    policy = ToolPolicy(
        "windows" if os.name == "nt" else "linux",
        frozenset({"workspace.write", "model.call", "secret.read"}),
        True,
        True,
        100_000,
    )
    expected = '{"summary": "approved test"}\n'
    studio, p, build, cases, verifier, policy = verification_case(
        tmp_path, expected=expected, payload=llm_ir(), policy=policy
    )
    called = []

    def fake_replay(package, root, source, python, *, runtime_env):
        called.append(runtime_env)
        assert runtime_env["WORKFLOW_STUDIO_SECRET_PROVIDER_KEY"] == "fixture-secret"
        folder = root / "output/artifacts/result"
        folder.mkdir(parents=True)
        (folder / "answer.json").write_bytes(expected.encode())
        (folder / "result.json").write_text(
            json.dumps({"result": {"path": "answer.json", "sha256": digest(expected.encode())}})
        )
        return {
            "named_outputs": {"result": {"primary": "artifacts/result/result.json"}},
            "run_id": "fixture-run",
            "lifecycle": ["run.finished"],
        }

    monkeypatch.setattr("workflow_studio.verifier.report.replay", fake_replay)
    denied = verifier.verify(p.project_id, build["build_id"], cases, policy)
    assert denied["error_code"] == "APPROVAL_REQUIRED"
    assert called == []
    scope = disclosure(studio, p.project_id, build["build_id"], cases)
    assert scope["maximum_budget_micro_usd"] == 100_000
    with pytest.raises(StudioError):
        environment(scope, RuntimeAuthorization("stale", {"provider_key": "fixture-secret"}))
    approved = RuntimeAuthorization(scope["request_hash"], {"provider_key": "fixture-secret"})
    report = verifier.verify(p.project_id, build["build_id"], cases, policy, authorization=approved)
    assert report["status"] == "passed", report
    assert report["runtime_model_authorized"] is True
    assert len(called) == 1
    assert "fixture-secret" not in json.dumps(report)
    assert "fixture-secret" not in repr(approved)
    studio.close()


def test_deterministic_scope_never_inherits_model_credentials():
    assert (
        environment({"models": []}, RuntimeAuthorization("unused", {"provider_key": "secret"}))
        == {}
    )
