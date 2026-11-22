"""Versioned interview answers owned by the existing Studio project."""

import hashlib
import json
from dataclasses import dataclass
from uuid import uuid4

from ..contracts import StudioError
from ..project.models import canonical_json
from ..project.service import now

# Parent domains determine which decisions need review after an upstream edit.
DOMAINS = {
    "goal": (),
    "success": ("goal",),
    "actors": ("goal",),
    "inputs": ("goal",),
    "outputs": ("success", "inputs"),
    "steps": ("inputs", "outputs"),
    "controls": ("steps",),
    "rules": ("goal",),
    "capabilities": ("steps", "rules"),
    "quality": ("success", "rules", "outputs"),
    "recovery": ("controls", "quality"),
    "review": ("actors", "quality", "recovery"),
    "budget": ("capabilities",),
    "preferences": ("outputs",),
    "cases": ("controls", "quality", "review", "preferences"),
    "evolution": ("rules", "cases"),
}


@dataclass(frozen=True)
class UserReply:
    """Returned only by the trusted local/host interaction adapter for this prompt."""

    value: object
    evidence_ref: str
    session_id: str
    domain: str
    revision: int
    request_id: str
    user_text: str | None = None


def digest(payload):
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def reject(message):
    raise StudioError("INPUT_INVALID", message)


def invalidate(db, state, domain, revision):
    affected = {domain}
    for key, parents in DOMAINS.items():
        if affected.intersection(parents):
            affected.add(key)
            item = state["coverage"][key]
            if item["status"] != "unknown":
                item.update(status="blocked", origin="upstream_changed", revision=revision)
    if "cases" in affected:
        state["stale_cases"] = [
            dict(row)
            for row in db.execute(
                "SELECT case_id,MAX(version) AS version FROM studio_test_cases WHERE project_id=? GROUP BY case_id",
                (state["project_id"],),
            )
        ]


def project_goal_changed(db, project_id, old, new, version):
    """Version edits from the older project API inside the caller's transaction."""
    row = db.execute(
        """SELECT r.snapshot_json FROM studio_interview_revisions r
        JOIN studio_interviews i USING(session_id) WHERE i.project_id=? ORDER BY r.revision DESC LIMIT 1""",
        (project_id,),
    ).fetchone()
    if not row:
        return
    state = json.loads(row[0])
    for domain, field in (("goal", "description"), ("success", "success_criteria")):
        if old.get(field) == new.get(field):
            continue
        invalidate(db, state, domain, version)
        state["coverage"][domain].update(
            value=new.get(field),
            status="inferred" if new.get(field) else "unknown",
            origin="project",
            evidence_refs=[project_id],
            revision=version,
        )
    state.update(phase="needs_confirmation", revision=version)
    db.execute(
        "INSERT INTO studio_interview_revisions VALUES (?,?,?,?,?,?)",
        (
            state["session_id"],
            version,
            "project-update-" + uuid4().hex,
            digest(new),
            canonical_json(state),
            now(),
        ),
    )


class Interviews:
    def __init__(self, studio):
        self.repo = studio.repository
        self.projects = studio.projects

    def create(self, name, task, request_id=None):
        if not isinstance(task, str) or not task.strip():
            reject("請提供任務內容。")
        if request_id is not None and (
            not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 128
        ):
            reject("建立專案的請求識別碼無效。")
        key = "create:" + request_id if request_id else "start"
        fingerprint = digest({"name": name, "task": task})
        with self.repo.transaction() as db:
            if request_id:
                row = db.execute(
                    "SELECT request_hash,snapshot_json FROM studio_interview_revisions WHERE request_id=?",
                    (key,),
                ).fetchone()
                if row:
                    if row[0] != fingerprint:
                        raise StudioError("REVISION_CONFLICT", "同一建立請求已用於不同任務。")
                    return json.loads(row[1])
            project = self.projects.create(name, {"description": task})
            return self._start(project.project_id, project.version, key, fingerprint)

    def start(self, project_id, expected_revision):
        return self._start(project_id, expected_revision, "start", digest({"project": project_id}))

    def _start(self, project_id, expected_revision, key, fingerprint):
        with self.repo.transaction() as db:
            project = self.projects._editable(db, project_id, expected_revision)
            if db.execute(
                "SELECT 1 FROM studio_interviews WHERE project_id=?", (project_id,)
            ).fetchone():
                return self.get(project_id)
            session = "interview_" + uuid4().hex
            db.execute("INSERT INTO studio_interviews VALUES (?,?)", (session, project_id))
            items = {
                key: {
                    "id": key,
                    "domain": key,
                    "value": None,
                    "origin": "none",
                    "evidence_refs": [],
                    "impact": "business",
                    "status": "unknown",
                    "revision": 0,
                }
                for key in DOMAINS
            }
            for domain, field in (("goal", "description"), ("success", "success_criteria")):
                seed = project.goal.get(field)
                if seed:
                    items[domain].update(
                        value=seed,
                        origin="project",
                        status="inferred",
                        evidence_refs=[project_id],
                        revision=project.version + 1,
                    )
            state = {
                "project_id": project_id,
                "session_id": session,
                "coverage": items,
                "phase": "collecting",
                "stale_cases": [],
            }
            self._save(db, state, project.version, key, fingerprint)
            return state

    def _latest(self, db, project_id):
        row = db.execute(
            """SELECT r.snapshot_json FROM studio_interview_revisions r
            JOIN studio_interviews i USING(session_id) WHERE i.project_id=?
            ORDER BY r.revision DESC LIMIT 1""",
            (project_id,),
        ).fetchone()
        if row is None:
            raise StudioError("INTERVIEW_NOT_FOUND", "此專案尚未建立訪談。")
        return json.loads(row[0])

    def get(self, project_id):
        with self.repo.transaction() as db:
            state = self._latest(db, project_id)
            project = self.projects.get(project_id)
            state["revision"] = project.version
            if project.status != "active":
                state["phase"] = project.status.value
            return state

    def history(self, project_id):
        return [
            json.loads(row[0])
            for row in self.repo.connection.execute(
                """
            SELECT r.snapshot_json FROM studio_interview_revisions r JOIN studio_interviews i USING(session_id)
            WHERE i.project_id=? ORDER BY r.revision""",
                (project_id,),
            )
        ]

    def _save(self, db, state, expected, request_id, request_hash):
        state["revision"] = expected + 1
        db.execute(
            "UPDATE studio_projects SET version=version+1,updated_at=? WHERE project_id=?",
            (now(), state["project_id"]),
        )
        db.execute(
            "INSERT INTO studio_interview_revisions VALUES (?,?,?,?,?,?)",
            (
                state["session_id"],
                state["revision"],
                request_id,
                request_hash,
                canonical_json(state),
                now(),
            ),
        )

    def _change(self, project_id, expected, request_id, parameters, edit):
        if not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 128:
            reject("必須提供具名請求識別碼。")
        if request_id.startswith("create:"):
            reject("此識別碼前綴保留給建立專案。")
        fingerprint = digest({"expected_revision": expected, "parameters": parameters})
        with self.repo.transaction() as db:
            state = self._latest(db, project_id)
            prior = db.execute(
                "SELECT request_hash,snapshot_json FROM studio_interview_revisions WHERE session_id=? AND request_id=?",
                (state["session_id"], request_id),
            ).fetchone()
            if prior is not None:
                if prior[0] != fingerprint:
                    raise StudioError("REVISION_CONFLICT", "同一請求已用於另一個回答。")
                return json.loads(prior[1])
            self.projects._editable(db, project_id, expected)
            edit(db, state)
            self._save(db, state, expected, request_id, fingerprint)
            return state

    def answer(
        self,
        project_id,
        expected_revision,
        request_id,
        domain,
        value,
        *,
        status="inferred",
        evidence_refs=(),
        impact="business",
        confidence=None,
        user_reply=None,
    ):
        data = self.answer_data(
            expected_revision,
            request_id,
            domain,
            value,
            status=status,
            evidence_refs=evidence_refs,
            impact=impact,
            confidence=confidence,
            user_reply=user_reply,
        )
        return self._save_answers(
            project_id, expected_revision, request_id, data, [(data, user_reply)]
        )

    @staticmethod
    def answer_data(
        expected_revision,
        request_id,
        domain,
        value,
        *,
        status="inferred",
        evidence_refs=(),
        impact="business",
        confidence=None,
        user_reply=None,
    ):
        if domain not in DOMAINS or status not in {"inferred", "defaulted", "blocked", "confirmed"}:
            reject("訪談領域或回答狀態無效。")
        if (
            impact not in {"low", "business", "high"}
            or not isinstance(evidence_refs, (list, tuple))
            or not all(isinstance(v, str) and v.strip() for v in evidence_refs)
        ):
            reject("影響等級或證據引用無效。")
        if confidence is not None and (
            type(confidence) not in (int, float) or not 0 <= confidence <= 1
        ):
            reject("信心值須在 0 到 1 之間。")
        if status == "confirmed":
            if (
                not isinstance(user_reply, UserReply)
                or not user_reply.evidence_ref.strip()
                or user_reply.domain != domain
                or user_reply.revision != expected_revision
                or user_reply.request_id != request_id
                or digest({"value": user_reply.value}) != digest({"value": value})
            ):
                raise StudioError("USER_CONFIRMATION_REQUIRED", "須有對應此回答的實際使用者回覆。")
            refs, origin = [*evidence_refs, user_reply.evidence_ref], "user"
        else:
            refs, origin = (
                list(evidence_refs),
                {"inferred": "host", "defaulted": "default", "blocked": "gap"}[status],
            )
        if value is None or (isinstance(value, str) and not value.strip()):
            reject("未回答不能轉為有效決策。")
        if status == "inferred" and (confidence is None or not refs):
            reject("推論須附信心值與來源。")
        if domain == "goal" and not isinstance(value, str):
            reject("工作目標需要文字。")
        if domain == "success" and not (
            isinstance(value, list)
            and value
            and all(isinstance(s, str) and s.strip() for s in value)
        ):
            reject("成功條件需要逐項文字。")
        if domain != "preferences" and impact == "low":
            impact = "business"
        data = {
            "id": domain,
            "domain": domain,
            "value": value,
            "origin": origin,
            "status": status,
            "impact": impact,
            "evidence_refs": refs,
            "confidence": confidence,
        }
        if user_reply is not None and user_reply.user_text is not None:
            data.update(origin="desktop_conversation", user_text=user_reply.user_text)
        return data

    def _save_answers(self, project_id, expected_revision, request_id, parameters, answers):
        def edit(db, state):
            if state["phase"] == "paused":
                raise StudioError("INTERVIEW_PAUSED", "請先恢復訪談。")
            items = state["coverage"]
            for data, user_reply in answers:
                domain, status, value = data["domain"], data["status"], data["value"]
                if status == "confirmed" and state["session_id"] != user_reply.session_id:
                    raise StudioError("USER_CONFIRMATION_REQUIRED", "回覆屬於其他訪談。")
                old = items[domain]
                if old["revision"] and (
                    old["value"] != value or old["status"] == "confirmed" and status != "confirmed"
                ):
                    invalidate(db, state, domain, expected_revision + 1)
                items[domain] = {**data, "revision": expected_revision + 1}
                if domain in {"goal", "success"}:
                    goal = self.projects.get(project_id).goal
                    goal["description" if domain == "goal" else "success_criteria"] = value
                    db.execute(
                        "UPDATE studio_projects SET goal_json=? WHERE project_id=?",
                        (canonical_json(goal), project_id),
                    )
                if domain == "cases" and status == "confirmed":
                    state["stale_cases"] = []
            state["phase"] = (
                "ready_to_export"
                if all(
                    i["status"] == "confirmed"
                    or i["status"] == "defaulted"
                    and i["impact"] == "low"
                    for i in items.values()
                )
                and not state["stale_cases"]
                else "needs_confirmation"
            )

        return self._change(project_id, expected_revision, request_id, parameters, edit)

    def pause(self, project_id, expected_revision, request_id, *, resume=False):
        def edit(db, state):
            if resume:
                if state["phase"] != "paused":
                    reject("訪談尚未暫停。")
                state["phase"] = state.pop("resume_phase")
            else:
                if state["phase"] == "paused":
                    reject("訪談已暫停。")
                state["resume_phase"], state["phase"] = state["phase"], "paused"

        return self._change(project_id, expected_revision, request_id, {"resume": resume}, edit)
