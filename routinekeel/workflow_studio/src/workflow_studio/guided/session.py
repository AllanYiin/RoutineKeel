"""Stage machine for one project's guided authoring session.

Agent-side calls (submit_draft, respond) and person-side calls (answer, reopen, commit) are
separate methods on purpose: the transport decides which channel may reach which method.
"""

import copy
import json

from ..contracts import StudioError
from ..governance.evidence import EvidenceRecords
from ..ir.validator import validate_ir
from ..project.models import canonical_json
from ..project.service import now
from .reflection import (
    AGENT_LABELS,
    RESOLUTIONS,
    build_cards,
    complete_technical_fields,
    reflection_items,
    validate_questions,
)

MAX_REFLECTION_ROUNDS = 2
MAX_REPAIRS = 2
SETTLED = {"auto", "confirmed", "chosen"}
EMPTY = {
    "stage": "empty",
    "round": 0,
    "draft": None,
    "items": [],
    "cards": [],
    "answers": {},
    "revision_id": None,
    "consented": [],
    "committed_by": None,
    "repairs": 0,
    "trial": None,
    "release": None,
}


class GuidedSessions:
    def __init__(self, studio, policy):
        self.studio, self.policy, self.repository = studio, policy, studio.repository

    def state(self, project_id):
        self.studio.projects.get(project_id)
        row = self.repository.connection.execute(
            "SELECT version,state_json FROM studio_guided_sessions WHERE project_id=?",
            (project_id,),
        ).fetchone()
        state = {**copy.deepcopy(EMPTY), **(json.loads(row[1]) if row else {})}
        state["cases"] = self._cases(project_id)
        return {"version": row[0] if row else 0, **state, "next": self._next(state)}

    def _cases(self, project_id):
        from ..verifier.golden_cases import GoldenCases

        return [
            {"case_id": c["case_id"], "version": c["version"], "name": c["specification"]["name"]}
            for c in GoldenCases(self.studio).list(project_id)
        ]

    @staticmethod
    def _next(state):
        stage = state["stage"]
        if stage == "empty":
            return {"actor": "agent", "action": "submit_draft"}
        if stage == "needs_reflection":
            fixable = [i for i in state["items"] if "keep" not in RESOLUTIONS[i["kind"]]]
            return {"actor": "agent", "action": "submit_draft" if fixable else "respond"}
        if stage == "blocked":
            return {"actor": "person", "action": "reopen"}
        if stage == "revising":
            return {"actor": "agent", "action": "submit_draft"}
        if stage == "cards":
            pending = [c["card_id"] for c in state["cards"] if c["state"] == "pending"]
            return {"actor": "person", "action": "answer" if pending else "commit"}
        if stage == "committed":
            if not state.get("cases"):
                return {"actor": "person", "action": "add_case"}
            return {"actor": "agent", "action": "trial"}
        if stage == "trial_failed":
            if state["repairs"] >= MAX_REPAIRS:
                return {"actor": "person", "action": "reopen"}
            return {"actor": "agent", "action": "submit_draft"}
        if stage == "trial_passed":
            return {"actor": "person", "action": "publish"}
        return {"actor": "nobody", "action": "done"}

    def _load(self, db, project_id, expected_version):
        row = db.execute(
            "SELECT version,state_json FROM studio_guided_sessions WHERE project_id=?",
            (project_id,),
        ).fetchone()
        if (row[0] if row else 0) != expected_version:
            raise StudioError("REVISION_CONFLICT", "引導階段已有更新，請重新讀取狀態。")
        return {**copy.deepcopy(EMPTY), **(json.loads(row[1]) if row else {})}

    def _store(self, db, project_id, expected_version, state):
        db.execute(
            "INSERT INTO studio_guided_sessions VALUES (?,?,?,?) ON CONFLICT(project_id) DO UPDATE "
            "SET version=excluded.version,state_json=excluded.state_json,updated_at=excluded.updated_at",
            (project_id, expected_version + 1, canonical_json(state), now()),
        )

    def _tools(self):
        return self.studio.tools.allowed(self.policy)

    def _validated(self, project_id, payload, tools):
        versions = {
            (row[0], row[1])
            for row in self.repository.connection.execute(
                """SELECT s.source_id,v.version FROM studio_sources s JOIN studio_source_versions v
                ON s.source_id=v.source_id WHERE s.project_id=?""",
                (project_id,),
            )
        }
        signatures = {key: tool.signature() for key, tool in tools.items()}
        return validate_ir(payload, signatures, source_versions=versions)

    def submit_draft(self, project_id, expected_version, draft):
        """Agent channel. Rejects anything that is not even a valid candidate."""
        if not isinstance(draft, dict) or not isinstance(draft.get("ir"), dict):
            raise StudioError("INPUT_INVALID", "草稿必須包含 ir。")
        evidence = draft["ir"].get("evidence")
        if not isinstance(evidence, list) or any(
            not isinstance(e, dict) or e.get("confidence_label") not in AGENT_LABELS
            for e in evidence
        ):
            raise StudioError(
                "EVIDENCE_INSUFFICIENT", "草稿的證據只能標為 observed、inferred 或 conflicted。"
            )
        tools = self._tools()
        payload = complete_technical_fields(draft["ir"], tools)
        ir = self._validated(project_id, payload, tools)
        questions = validate_questions(draft.get("questions"), ir)
        items = reflection_items(self.studio, project_id, ir, tools, questions)
        with self.repository.transaction() as db:
            state = self._load(db, project_id, expected_version)
            if state["stage"] == "blocked":
                raise StudioError("EVIDENCE_INSUFFICIENT", "反思已達上限，需由作者補充資料後重開。")
            repairing = state["stage"] == "trial_failed"
            if repairing and state["repairs"] >= MAX_REPAIRS:
                raise StudioError("TEST_FAILED", "修復已達兩輪，需由作者檢視失敗原因後決定。")
            state["repairs"] = state["repairs"] + 1 if repairing else 0
            state["round"] = state["round"] + 1 if state["stage"] == "needs_reflection" else 1
            state["draft"] = {"ir": payload, "questions": questions}
            state["items"], state["revision_id"] = items, None
            if not items:
                self._enter_cards(state, ir, tools)
                if repairing and not {c["state"] for c in state["cards"]} - SETTLED:
                    # A repair that leaves every confirmed statement untouched changes only how
                    # the work is done; the trial and the final approval still guard the result.
                    self._commit(db, state, project_id, state["committed_by"])
            elif state["round"] > MAX_REFLECTION_ROUNDS:
                state["stage"], state["cards"] = "blocked", []
            else:
                state["stage"], state["cards"] = "needs_reflection", []
            self._store(db, project_id, expected_version, state)
        return self.state(project_id)

    @staticmethod
    def _enter_cards(state, ir, tools):
        invalid = {i["target"] for i in state["items"] if i["kind"] == "locator_invalid"}
        cards = build_cards(ir, tools, state["draft"]["questions"], invalid)
        answers = {}
        for card in cards:
            prior = state["answers"].get(card["card_id"])
            if not prior or card["state"] != "pending" and prior["action"] != "choose":
                continue
            if prior["action"] == "confirm" and prior["content_hash"] == card["content_hash"]:
                card["state"] = "confirmed"
            elif prior["action"] == "choose" and prior["value"] in {
                card.get("drafted"),
                card["text"],
            }:
                # The agent applied the person's selection verbatim, so it stays answered.
                card["state"] = "chosen"
            else:
                continue
            answers[card["card_id"]] = prior
        state["stage"], state["cards"], state["answers"] = "cards", cards, answers

    def respond(self, project_id, expected_version, responses):
        """Agent channel. Only items that may be kept can be answered; the rest need a new draft."""
        responses = responses if isinstance(responses, dict) else {}
        with self.repository.transaction() as db:
            state = self._load(db, project_id, expected_version)
            if state["stage"] != "needs_reflection":
                raise StudioError("INPUT_INVALID", "目前沒有待回應的反思清單。")
            for item in state["items"]:
                note = responses.get(item["item_id"])
                if "keep" not in RESOLUTIONS[item["kind"]]:
                    raise StudioError(
                        "EVIDENCE_INSUFFICIENT", f"{item['kind']} 必須修正草稿後重新提交。"
                    )
                if not isinstance(note, str) or not 1 <= len(note.strip()) <= 2000:
                    raise StudioError("INPUT_INVALID", "保留項目需附 1–2,000 字的理由。")
                item["kept_reason"] = note.strip()
            tools = self._tools()
            ir = self._validated(project_id, state["draft"]["ir"], tools)
            self._enter_cards(state, ir, tools)
            self._store(db, project_id, expected_version, state)
        return self.state(project_id)

    def answer(self, project_id, expected_version, card_id, action, value=None):
        """Person channel."""
        with self.repository.transaction() as db:
            state = self._load(db, project_id, expected_version)
            card = next((c for c in state["cards"] if c["card_id"] == card_id), None)
            if state["stage"] != "cards" or card is None or card["state"] != "pending":
                raise StudioError("INPUT_INVALID", "這張卡片目前不能回答。")
            if card["kind"] == "confirm" and action == "confirm":
                card["state"] = "confirmed"
            elif (
                card["kind"] == "confirm"
                and action == "modify"
                and isinstance(value, str)
                and 1 <= len(value.strip()) <= 2000
            ):
                card["state"], value = "modified", value.strip()
            elif card["kind"] == "question" and action == "choose" and value in card["options"]:
                card["state"] = "answered"
            elif (
                card["kind"] == "choice"
                and action == "choose"
                and value in {o["key"] for o in card["options"]}
            ):
                card["state"] = "chosen" if value == card["drafted"] else "answered"
            else:
                raise StudioError("INPUT_INVALID", "這張卡片不接受此回答。")
            state["answers"][card_id] = {
                "action": action,
                "value": value,
                "content_hash": card["content_hash"],
            }
            unsettled = {c["state"] for c in state["cards"]} - SETTLED
            if unsettled and "pending" not in unsettled:
                # Every card has been seen; the agent now has to apply the corrections.
                state["stage"] = "revising"
            self._store(db, project_id, expected_version, state)
        return self.state(project_id)

    def consent(self, project_id, expected_version, source_ids):
        """Person channel: source contents reach the host model only after this."""
        known = {s.source_id for s in self.studio.sources.list(project_id)}
        if not isinstance(source_ids, list) or not set(source_ids) <= known:
            raise StudioError("INPUT_INVALID", "只能同意這個專案目前的來源。")
        with self.repository.transaction() as db:
            state = self._load(db, project_id, expected_version)
            state["consented"] = sorted(set(source_ids))
            self._store(db, project_id, expected_version, state)
        return self.state(project_id)

    def reopen(self, project_id, expected_version):
        """Person channel: allow new drafts after reflection ran out, e.g. once sources were added."""
        with self.repository.transaction() as db:
            state = self._load(db, project_id, expected_version)
            if state["stage"] == "trial_failed" and state["repairs"] >= MAX_REPAIRS:
                state["repairs"] = 0
            elif state["stage"] == "blocked":
                state.update(stage="empty", round=0, items=[], draft=None)
            else:
                raise StudioError("INPUT_INVALID", "目前不需要重開。")
            self._store(db, project_id, expected_version, state)
        return self.state(project_id)

    def commit(self, project_id, expected_version, *, actor):
        """Person channel: turn the confirmed draft into an immutable revision."""
        with self.repository.transaction() as db:
            state = self._load(db, project_id, expected_version)
            if state["stage"] != "cards" or {c["state"] for c in state["cards"]} - SETTLED:
                raise StudioError("APPROVAL_REQUIRED", "還有卡片尚未確認。")
            self._commit(db, state, project_id, actor)
            self._store(db, project_id, expected_version, state)
        return self.state(project_id)

    def _commit(self, db, state, project_id, actor):
        payload = copy.deepcopy(state["draft"]["ir"])
        decided = {
            c["target"]: c["state"]
            for c in state["cards"]
            if c["card_id"].startswith("evidence:") and c["state"] != "auto"
        }
        records = EvidenceRecords(self.repository)
        for evidence in payload["evidence"]:
            if evidence["id"] in decided:
                evidence["confidence_label"] = "human_confirmed"
                evidence["verified_by"] = records.record(
                    project_id,
                    evidence,
                    actor=actor,
                    reason="問題卡選擇" if decided[evidence["id"]] == "chosen" else "確認卡確認",
                )
        accepted = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT ir_json FROM studio_revisions WHERE project_id=? AND status<>'draft'",
                (project_id,),
            )
        ]
        payload["revision"] = 1 + max(
            (item["revision"] for item in accepted if item.get("ir_version") == "1.0"),
            default=0,
        )
        project = self.studio.projects.get(project_id)
        project = self.studio.projects.update(project_id, project.version, goal=payload["goal"])
        revision = self.studio.save_ir(project_id, project.version, payload, self.policy)
        state.update(
            stage="committed", revision_id=revision.revision_id, committed_by=actor, trial=None
        )

    def add_case(self, project_id, expected_version, source_id, input_path, expected_path):
        """Person channel: a known-good input and result that every trial must reproduce."""
        from ..verifier.golden_cases import GoldenCases

        source = self.studio.sources.get(project_id, source_id)

        def ref(path):
            record = next((r for r in source.manifest["files"] if r["path"] == path), None)
            if record is None:
                raise StudioError("INPUT_INVALID", "案例檔案必須來自這份材料。")
            return {
                "source_id": source.source_id,
                "version": source.version,
                "path": path,
                "sha256": record["sha256"],
            }

        with self.repository.transaction() as db:
            state = self._load(db, project_id, expected_version)
            GoldenCases(self.studio).save(
                project_id,
                self.studio.projects.get(project_id).version,
                {
                    "name": f"{input_path} → {expected_path}",
                    "input": ref(input_path),
                    "expected": ref(expected_path),
                    "comparison": "exact_bytes",
                },
            )
            if state["stage"] in {"trial_passed", "trial_failed"}:
                # Earlier results did not cover this case.
                state.update(stage="committed", trial=None)
            self._store(db, project_id, expected_version, state)
        return self.state(project_id)

    def trial(self, project_id, expected_version, runtime_python=None):
        """Agent channel: build the committed revision and replay every case in isolation."""
        from ..verifier.report import Verifier

        state = self.state(project_id)
        if state["version"] != expected_version:
            raise StudioError("REVISION_CONFLICT", "引導階段已有更新，請重新讀取狀態。")
        if state["stage"] != "committed":
            raise StudioError("INPUT_INVALID", "要先由作者確認並存成版本，才能試做。")
        if not state["cases"]:
            raise StudioError("TEST_FAILED", "還沒有試做案例；請作者在頁面指定輸入與正確成果。")
        outputs = list(state["draft"]["ir"]["outputs"])
        if len(outputs) != 1:
            raise StudioError("IR_UNSUPPORTED_FEATURE", "引導流程目前只支援單一交付物的試做。")
        # Building and replaying take seconds and start a subprocess, so no transaction is held.
        build = self.studio.compile(project_id, state["revision_id"], self.policy)
        bindings = [
            {"case_id": c["case_id"], "version": c["version"], "output": outputs[0]}
            for c in state["cases"]
        ]
        report = Verifier(self.studio, runtime_python).verify(
            project_id, build["build_id"], bindings, self.policy
        )
        names = {c["case_id"]: c["name"] for c in state["cases"]}
        summary = {
            "build_id": build["build_id"],
            "package_hash": build["package_hash"],
            "status": report["status"],
            "message": report.get("message"),
            "cases": [
                {
                    "name": names[c["case_id"]],
                    "status": c["status"],
                    "text_diff": c.get("difference", {}).get("text_diff"),
                }
                for c in report["cases"]
            ],
        }
        with self.repository.transaction() as db:
            stored = self._load(db, project_id, expected_version)
            stored["trial"] = summary
            stored["stage"] = "trial_passed" if report["status"] == "passed" else "trial_failed"
            self._store(db, project_id, expected_version, stored)
        return self.state(project_id)

    def publish(self, project_id, expected_version, *, actor, target_workspace):
        """Person channel: approval is bound to the package that passed the trial."""
        from ..governance.approvals import Approvals
        from ..governance.release import Releases

        state = self.state(project_id)
        if state["version"] != expected_version:
            raise StudioError("REVISION_CONFLICT", "引導階段已有更新，請重新讀取狀態。")
        if state["stage"] != "trial_passed":
            raise StudioError("TEST_FAILED", "試做通過後才能核准發布。")
        trial = state["trial"]
        approval = Approvals(self.studio).approve(
            project_id,
            trial["build_id"],
            trial["package_hash"],
            self.policy,
            actor=actor,
            reason="在引導頁面核對試做成果後核准",
        )
        releases = Releases(self.studio)
        workflow_id = state["draft"]["ir"]["workflow_id"]
        current = releases.current(target_workspace, workflow_id)
        release = releases.publish(
            project_id,
            approval["approval_id"],
            target_workspace,
            self.policy,
            expected_current=current["release_id"] if current else None,
        )
        with self.repository.transaction() as db:
            stored = self._load(db, project_id, expected_version)
            stored["stage"] = "published"
            stored["release"] = {
                "release_id": release["release_id"],
                "workflow_id": release["workflow_id"],
                "approved_by": actor.strip(),
            }
            self._store(db, project_id, expected_version, stored)
        return self.state(project_id)
