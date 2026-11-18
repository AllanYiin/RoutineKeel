import os

import pytest
from workflow_studio.contracts import StudioError
from workflow_studio.registry.policy import ToolPolicy
from workflow_studio.service import StudioService

pytest.importorskip("PIL", reason="Guided session fixtures require Studio source dependencies")

POLICY = ToolPolicy("windows" if os.name == "nt" else "linux")


def guided_project(tmp_path, text="把輸入檔案完整複製成成果\n重複的資料以最後一筆為準\n"):
    studio = StudioService.open_workspace(tmp_path / "studio")
    project = studio.projects.create("引導")
    source = studio.sources.import_text(project.project_id, 1, text, name="說明.txt")
    studio.tools.seed()
    return studio, project, source, studio.guided(POLICY)


def sketch(source, *, evidence=None, tool="file.copy", locator="說明.txt:1"):
    """What an agent submits: business content only, no cache/retry/timeout/platform fields."""
    return {
        "workflow_id": "guided-copy",
        "revision": 1,
        "goal": {"description": "完整複製輸入檔案", "success_criteria": ["成果與輸入相同"]},
        "inputs": {"source": {"type": "file"}},
        "outputs": {"result": "artifact://copy/result"},
        "evidence": evidence
        or [
            {
                "id": "copy-rule",
                "source_id": source.source_id,
                "source_version": source.version,
                "evidence_locator": locator,
                "claim": "把輸入檔案完整複製成成果",
                "confidence_label": "observed",
            }
        ],
        "nodes": [
            {
                "id": "copy",
                "tool": tool,
                "inputs": {"source": {"source": "input://source", "port": {"type": "file"}}},
                "outputs": {"result": {"type": "file", "path": "artifacts/result.txt"}},
                "evidence_refs": [(evidence or [{"id": "copy-rule"}])[0]["id"]],
            }
        ],
    }


def rule(source, identity, claim, label, line):
    return {
        "id": identity,
        "source_id": source.source_id,
        "source_version": source.version,
        "evidence_locator": f"說明.txt:{line}",
        "claim": claim,
        "confidence_label": label,
    }


def cards(state):
    return {card["card_id"]: card for card in state["cards"]}


def test_observed_fact_is_filled_and_person_only_confirms_goal(tmp_path):
    studio, p, source, guided = guided_project(tmp_path)
    state = guided.submit_draft(p.project_id, 0, {"ir": sketch(source)})
    assert state["stage"] == "cards" and state["items"] == []
    node = state["draft"]["ir"]["nodes"][0]
    assert node["retry_policy"] == {"mode": "never", "max_attempts": 1}
    assert node["cache_policy"]["mode"] == "content_addressed" and node["timeout_seconds"] == 60
    shown = cards(state)
    assert shown["evidence:copy-rule"]["state"] == "auto"
    assert [c for c in state["cards"] if c["state"] == "pending"] == [shown["goal"]]
    with pytest.raises(StudioError) as early:
        guided.commit(p.project_id, state["version"], actor="作者")
    assert early.value.code == "APPROVAL_REQUIRED"
    state = guided.answer(p.project_id, state["version"], "goal", "confirm")
    assert state["next"] == {"actor": "person", "action": "commit"}
    state = guided.commit(p.project_id, state["version"], actor="作者")
    revision = studio.projects.get_revision(p.project_id, state["revision_id"])
    assert state["stage"] == "committed" and revision.status.value == "ir_ready"
    assert revision.ir["evidence"][0]["confidence_label"] == "observed"
    assert studio.compile(p.project_id, revision.revision_id, POLICY)["package_hash"]
    studio.close()


def test_agent_cannot_grant_confirmed_labels_or_use_unlisted_tools(tmp_path):
    studio, p, source, guided = guided_project(tmp_path)
    forged = rule(source, "copy-rule", "把輸入檔案完整複製成成果", "human_confirmed", 1)
    forged["verified_by"] = "evidence_decision_forged"
    with pytest.raises(StudioError) as caught:
        guided.submit_draft(p.project_id, 0, {"ir": sketch(source, evidence=[forged])})
    assert caught.value.code == "EVIDENCE_INSUFFICIENT"
    with pytest.raises(StudioError) as caught:
        guided.submit_draft(p.project_id, 0, {"ir": sketch(source, tool="shell.run")})
    assert caught.value.code == "TOOL_NOT_ALLOWED"
    assert guided.state(p.project_id)["stage"] == "empty"
    studio.close()


def test_bad_locator_blocks_cards_until_fixed_and_two_rounds_is_the_limit(tmp_path):
    studio, p, source, guided = guided_project(tmp_path)
    bad = {"ir": sketch(source, locator="說明.txt:99")}
    state = guided.submit_draft(p.project_id, 0, bad)
    assert state["stage"] == "needs_reflection" and state["cards"] == []
    assert [i["kind"] for i in state["items"]] == ["locator_invalid"]
    with pytest.raises(StudioError):
        guided.respond(p.project_id, state["version"], {state["items"][0]["item_id"]: "保留"})
    state = guided.submit_draft(p.project_id, state["version"], bad)
    assert state["stage"] == "needs_reflection" and state["round"] == 2
    state = guided.submit_draft(p.project_id, state["version"], bad)
    assert state["stage"] == "blocked" and state["next"]["actor"] == "person"
    with pytest.raises(StudioError):
        guided.submit_draft(p.project_id, state["version"], {"ir": sketch(source)})
    state = guided.reopen(p.project_id, state["version"])
    state = guided.submit_draft(p.project_id, state["version"], {"ir": sketch(source)})
    assert state["stage"] == "cards"
    studio.close()


def test_inferred_rule_needs_a_person_and_modification_returns_to_agent(tmp_path):
    studio, p, source, guided = guided_project(tmp_path)
    evidence = [
        rule(source, "copy-rule", "把輸入檔案完整複製成成果", "observed", 1),
        rule(source, "dedupe", "重複的資料以第一筆為準", "inferred", 2),
    ]
    state = guided.submit_draft(p.project_id, 0, {"ir": sketch(source, evidence=evidence)})
    assert cards(state)["evidence:dedupe"]["kind"] == "confirm"
    state = guided.answer(p.project_id, state["version"], "goal", "confirm")
    state = guided.answer(
        p.project_id, state["version"], "evidence:dedupe", "modify", "重複的資料以最後一筆為準"
    )
    assert state["stage"] == "revising" and state["next"]["actor"] == "agent"
    evidence[1]["claim"] = "重複的資料以最後一筆為準"
    state = guided.submit_draft(
        p.project_id, state["version"], {"ir": sketch(source, evidence=evidence)}
    )
    shown = cards(state)
    assert shown["goal"]["state"] == "confirmed", "unchanged confirmations are not asked twice"
    assert shown["evidence:dedupe"]["state"] == "pending"
    state = guided.answer(p.project_id, state["version"], "evidence:dedupe", "confirm")
    state = guided.commit(p.project_id, state["version"], actor="作者")
    saved = studio.projects.get_revision(p.project_id, state["revision_id"]).ir["evidence"]
    confirmed = next(e for e in saved if e["id"] == "dedupe")
    assert confirmed["confidence_label"] == "human_confirmed"
    assert (
        studio.evidence(POLICY).records.get(p.project_id, confirmed["verified_by"])["actor"]
        == "作者"
    )
    studio.close()


def test_conflict_requires_question_and_chosen_option_becomes_the_rule(tmp_path):
    studio, p, source, guided = guided_project(tmp_path)
    evidence = [
        rule(source, "copy-rule", "把輸入檔案完整複製成成果", "observed", 1),
        rule(source, "dedupe", "重複資料的處理方式不一致", "conflicted", 2),
    ]
    state = guided.submit_draft(p.project_id, 0, {"ir": sketch(source, evidence=evidence)})
    assert [i["kind"] for i in state["items"]] == ["conflict_needs_question"]
    question = {
        "evidence_id": "dedupe",
        "prompt": "同一筆資料重複時要留哪一筆？",
        "why": "說明寫最後一筆，但案例留的是第一筆。",
        "options": ["重複的資料以最後一筆為準", "重複的資料以第一筆為準"],
    }
    draft = {"ir": sketch(source, evidence=evidence), "questions": [question]}
    state = guided.submit_draft(p.project_id, state["version"], draft)
    assert cards(state)["evidence:dedupe"]["kind"] == "question"
    with pytest.raises(StudioError):
        guided.answer(p.project_id, state["version"], "evidence:dedupe", "choose", "都不要")
    state = guided.answer(p.project_id, state["version"], "goal", "confirm")
    state = guided.answer(
        p.project_id, state["version"], "evidence:dedupe", "choose", question["options"][0]
    )
    assert state["stage"] == "revising"
    evidence[1].update(claim=question["options"][0], confidence_label="inferred")
    state = guided.submit_draft(
        p.project_id, state["version"], {"ir": sketch(source, evidence=evidence)}
    )
    assert cards(state)["evidence:dedupe"]["state"] == "chosen"
    state = guided.commit(p.project_id, state["version"], actor="作者")
    saved = studio.projects.get_revision(p.project_id, state["revision_id"]).ir["evidence"]
    assert next(e for e in saved if e["id"] == "dedupe")["confidence_label"] == "human_confirmed"
    studio.close()


def test_stale_version_is_refused_and_state_survives_reopen(tmp_path):
    studio, p, source, guided = guided_project(tmp_path)
    state = guided.submit_draft(p.project_id, 0, {"ir": sketch(source)})
    with pytest.raises(StudioError) as caught:
        guided.answer(p.project_id, 0, "goal", "confirm")
    assert caught.value.code == "REVISION_CONFLICT"
    studio.close()
    with StudioService.open_workspace(tmp_path / "studio") as reopened:
        assert reopened.guided(POLICY).state(p.project_id) == state


def test_choice_card_needs_same_job_with_different_tradeoffs():
    from workflow_studio.guided.reflection import build_cards, complete_technical_fields
    from workflow_studio.ir.validator import validate_ir
    from workflow_studio.registry.builtin import builtin_tools

    tools = {f"{t.tool_id}@{t.version}": t for t in builtin_tools()}
    copy = tools["file.copy@1.0.0"]
    paid = copy.model_copy(
        update={"tool_id": "file.copy_checked", "title": "複製並人工覆核", "cost_micro_usd": 5}
    )

    class Source:
        source_id, version = "source_fixture", 1

    payload = complete_technical_fields(sketch(Source), tools)
    signatures = {key: tool.signature() for key, tool in tools.items()}
    ir = validate_ir(payload, signatures)
    assert not [c for c in build_cards(ir, tools, {}, set()) if c["kind"] == "choice"]
    tools["file.copy_checked@1.0.0"] = paid
    choice = next(c for c in build_cards(ir, tools, {}, set()) if c["kind"] == "choice")
    assert [o["key"] for o in choice["options"]] == ["file.copy@1.0.0", "file.copy_checked@1.0.0"]
    assert [o["facts"]["cost_micro_usd"] for o in choice["options"]] == [0, 5]
    assert choice["drafted"] == "file.copy@1.0.0" and "recommended" not in choice
