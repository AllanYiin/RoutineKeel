import json

import pytest
from compiler_helpers import execute_package
from generated_cases import csv_parameters
from test_generated_handlers import program_ir
from workflow_studio.contracts import StudioError
from workflow_studio.llm.provider import Chunk
from workflow_studio.llm.usage import Budget
from workflow_studio.registry.policy import ToolPolicy
from workflow_studio.service import StudioService


class HandlerProvider:
    model = "generated-handler-fixture"
    destination = "fixture://local"
    external = False

    def __init__(self, unsafe=False):
        self.unsafe = unsafe
        self.calls = 0

    def stream(self, messages, maximum):
        self.calls += 1
        context = json.loads(messages[1]["content"])
        assert "handler_generation" in context
        assert "data.transform_csv@1.0.0" in context["handler_generation"]["tools"]
        parameters = csv_parameters()
        if self.unsafe:
            parameters["program"] = (
                'def transform(text, params):\n    return open("private").read()'
            )
        payload = program_ir(parameters, "csv")
        payload.update(
            goal=context["goal"],
            workflow_id=context["workflow_id"],
            revision=context["next_revision"],
        )
        source = context["sources"][0]
        payload["evidence"][0].update(
            source_id=source["source_id"],
            source_version=source["source_version"],
            evidence_locator=source["path"] + ":1",
        )
        result = json.dumps(
            {"analysis": "產生新的訂單篩選 Handler 與合成測試。", "ir": payload, "questions": []},
            ensure_ascii=False,
        )
        for i in range(0, len(result), 100):
            yield Chunk(text=result[i : i + 100])
        yield Chunk(done=True)


@pytest.mark.parametrize("unsafe", [False, True])
def test_model_handler_proposal_is_validated_without_manual_code_edits(tmp_path, unsafe):
    with StudioService.open_workspace(tmp_path / "studio") as studio:
        p = studio.projects.create(
            "訂單篩選",
            {
                "description": "篩選未結帳且金額至少100的訂單",
                "success_criteria": ["只輸出符合條件的 id 與 amount"],
            },
        )
        source = studio.sources.import_text(
            p.project_id, 1, "CSV 欄位 id,amount,status；只保留 status=open 且 amount>=100。"
        )
        studio.tools.seed()
        policy = ToolPolicy("windows")
        provider = HandlerProvider(unsafe)
        planner = studio.discovery(provider, Budget(5, 1_000_000, 8000, 1000, 1000), policy)
        prepared = planner.prepare(p.project_id, [source.source_id])
        if unsafe:
            with pytest.raises(StudioError):
                list(planner.run(prepared))
            assert studio.projects.revisions(p.project_id) == []
            assert provider.calls == 3  # Initial proposal plus at most two repairs.
            return
        events = list(planner.run(prepared))
        assert provider.calls == 1
        revision = studio.projects.get_revision(p.project_id, events[-1]["revision_id"])
        assert revision.status == "ir_ready"
        build = studio.compile(p.project_id, revision.revision_id, policy)
        report = execute_package(
            build["path"] / "package",
            tmp_path / "runtime",
            b"id,amount,status\nA,200,open\nB,50,open\n",
        )
        assert report["token_usage"]["registered_requests"] == 0
        assert (
            tmp_path / "runtime/output/artifacts/result/artifacts/result.csv"
        ).read_text() == "id,amount\nA,200\n"


def test_model_repairs_invalid_generated_handler_within_same_scope(tmp_path):
    class RepairingProvider(HandlerProvider):
        def stream(self, messages, maximum):
            self.unsafe = self.calls == 0
            if self.calls:
                assert messages[-1]["role"] == "user"
                assert "CONTRACT_MISMATCH" in messages[-1]["content"]
            yield from super().stream(messages, maximum)

    with StudioService.open_workspace(tmp_path) as studio:
        p = studio.projects.create(
            "修正測試", {"description": "篩選訂單", "success_criteria": ["保留符合規則的資料"]}
        )
        source = studio.sources.import_text(p.project_id, 1, "amount>=100 且 status=open")
        studio.tools.seed()
        provider = RepairingProvider()
        planner = studio.discovery(
            provider, Budget(3, 1_000_000, 8000, 1000, 1000), ToolPolicy("windows")
        )
        events = list(planner.run(planner.prepare(p.project_id, [source.source_id])))
        assert provider.calls == 2
        assert len([e for e in events if e["event"] == "analysis.repairing"]) == 1
        assert events[-1]["event"] == "analysis.completed"
        revisions = studio.projects.revisions(p.project_id)
        assert len(revisions) == 1
        assert revisions[0].ir["goal"] == p.goal
        assert planner.ledger.summary(p.project_id)["calls"] == 2


def test_disabled_generation_capabilities_are_not_advertised(tmp_path):
    with StudioService.open_workspace(tmp_path) as studio:
        p = studio.projects.create(
            "工具範圍", {"description": "複製檔案", "success_criteria": ["內容相同"]}
        )
        source = studio.sources.import_text(p.project_id, 1, "複製檔案")
        studio.tools.seed()
        for suffix in ("text", "csv", "json"):
            studio.tools.set_status("data.transform_" + suffix, "1.0.0", 1, "disabled")
        planner = studio.discovery(
            HandlerProvider(), Budget(3, 1_000_000, 8000, 1000, 1000), ToolPolicy("windows")
        )
        context = json.loads(
            json.loads(planner.prepare(p.project_id, [source.source_id]).messages_json)[1][
                "content"
            ]
        )
        assert "handler_generation" not in context
