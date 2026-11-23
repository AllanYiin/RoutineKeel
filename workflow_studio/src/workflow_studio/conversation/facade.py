"""Short, text-first requests. The host streams model output as it arrives."""

from dataclasses import asdict
from pathlib import Path

from ..contracts import StudioError
from .interviews import DOMAINS, UserReply, digest

QUESTIONS = {
    "goal": "希望這個工作流幫你完成什麼？",
    "success": "看到哪些結果，你就會判定工作完成了？",
    "actors": "誰會提供資料、操作流程或驗收結果？",
    "inputs": "每次開始時會提供哪些資料？",
    "outputs": "完成時要交付哪些檔案或內容？",
    "steps": "從輸入到交付，平常會依序做哪些事？",
    "controls": "遇到哪些情況要改走另一條路、逐筆處理或重做？",
    "rules": "哪些業務規則一定要保留？",
    "capabilities": "哪些步驟需要工具、模型或外部服務？",
    "quality": "用哪些條件檢查成果品質？",
    "recovery": "失敗時可以重試幾次，何時交給人處理？",
    "review": "哪些成果或操作需要你親自確認？",
    "budget": "時間、費用與模型用量各有什麼上限？",
    "preferences": "語言、命名與交付格式有什麼偏好？",
    "cases": "請舉一個成功例子及一個應該阻擋的例子。",
    "evolution": "下次修改工作方法時，哪些變更需要你同意？",
}


def next_question(state, domain=None):
    counts = {status: 0 for status in ("unknown", "inferred", "confirmed", "blocked", "defaulted")}
    for item in state["coverage"].values():
        counts[item["status"]] += 1
    result = {k: state[k] for k in ("project_id", "session_id", "revision", "phase")}
    result.update(
        counts=counts,
        stream_instruction="請在宿主對話逐段呈現模型產生的提問、建議與附件摘要。",
        next_actions=["status", "history"],
    )
    if domain is not None and domain not in DOMAINS:
        raise StudioError("INPUT_INVALID", "找不到這個訪談領域。")
    if state["phase"] in {"paused", "archived", "deleted"}:
        return {
            **result,
            "text": "訪談已保存；恢復後可以繼續。",
            "next_actions": ["resume" if state["phase"] == "paused" else "restore"],
        }
    if domain is None and state["phase"] == "exported":
        return {
            **result,
            "text": "已匯出已確認的藍圖，可檢視交付或開始下一版修訂。",
            "next_actions": ["get_blueprint", "revise"],
        }
    if domain is None:
        pending = [
            key
            for key in DOMAINS
            if state["coverage"][key]["status"] != "confirmed"
            and not (
                state["coverage"][key]["status"] == "defaulted"
                and state["coverage"][key]["impact"] == "low"
            )
        ]
        # A deferred answer stays visible and blocks export. Ask another available question first.
        domain = next(
            (key for key in pending if state["coverage"][key]["status"] != "blocked"),
            pending[0] if pending else None,
        )
    if domain is None:
        return {
            **result,
            "text": "訪談已完成，請檢視交付條件與藍圖摘要。",
            "next_actions": ["status", "export"],
        }
    item = state["coverage"][domain]
    return {
        **result,
        "domain": domain,
        "text": QUESTIONS[domain],
        "status": item["status"],
        "suggested_answer": item["value"],
        "evidence_refs": item["evidence_refs"],
        "origin": item["origin"],
        "question_id": digest(
            {
                "session": state["session_id"],
                "revision": state["revision"],
                "domain": domain,
                "proposal": item["value"],
            }
        ),
        "free_text": True,
        "how_to_reply": "直接輸入你的規則；也可以指定前一題修正、暫停或先保留缺口。",
        "next_actions": ["reply", "revise", "skip", "pause", "status"],
    }


def next_questions(state, domains=None):
    first = next_question(state)
    if "question_id" not in first:
        return {**first, "questions": []}
    if domains is None:
        priorities = ("inputs", "outputs", "rules", "review", "preferences", *DOMAINS)
        domains = [
            key
            for key in dict.fromkeys(priorities)
            if state["coverage"][key]["status"] in {"unknown", "inferred"}
        ][:5] or [first["domain"]]
    if (
        not isinstance(domains, list)
        or not domains
        or any(not isinstance(k, str) or k not in DOMAINS for k in domains)
        or len(set(domains)) != len(domains)
    ):
        raise StudioError("INPUT_INVALID", "請提供不同的訪談領域。")
    return {
        **{key: first[key] for key in ("project_id", "session_id", "revision", "phase", "counts")},
        "questions": [next_question(state, key) for key in domains],
        "next_actions": ["reply_batch", "pause", "status"],
    }


def stream_text(deltas):
    """Forward genuine host/provider deltas; aborted streams have no completion event."""
    for index, text in enumerate(deltas):
        if not isinstance(text, str):
            raise StudioError("INPUT_INVALID", "串流事件須提供文字片段。")
        yield {"event": "delta", "sequence": index, "text": text}
    yield {"event": "completed"}


def check_user_text(user_text):
    if not isinstance(user_text, str) or not user_text.strip() or len(user_text) > 65536:
        raise StudioError("INPUT_INVALID", "需要使用者在對話中實際送出的回覆文字。")


def reply_state(studio, project_id, expected_revision):
    state = studio.interviews.get(project_id)
    if state["revision"] != expected_revision:
        # A retry must resolve against the same displayed proposal.
        rows = studio.interviews.history(project_id)
        previous = next((s for s in reversed(rows) if s["revision"] <= expected_revision), None)
        if previous is None:
            raise StudioError("REVISION_CONFLICT", "請重新取得目前的問題。")
        state = {**previous, "revision": expected_revision}
    return state


def chat_answer(
    state, request_id, user_text, ref, domain, question_id, response_kind="answer", answer_text=None
):
    if response_kind not in {"answer", "accept_proposal", "decline", "defer"}:
        raise StudioError("INPUT_INVALID", "無法辨識這次回覆的用途。")
    question = next_question(state, domain)
    if not question_id or question.get("question_id") != question_id:
        raise StudioError("REVISION_CONFLICT", "回覆所對應的問題或草稿已變更。")
    if answer_text is None:
        answer_text = user_text
    if not isinstance(answer_text, str) or not answer_text.strip() or answer_text not in user_text:
        raise StudioError("INPUT_INVALID", "答案須引用使用者回覆中的原文。")
    if response_kind in {"decline", "defer"}:
        return {"value": answer_text, "status": "blocked", "evidence_refs": [ref]}
    value = (
        question["suggested_answer"]
        if response_kind == "accept_proposal"
        else ([answer_text] if domain == "success" else answer_text)
    )
    reply = UserReply(
        value, ref, state["session_id"], domain, state["revision"], request_id, user_text
    )
    return {"value": value, "status": "confirmed", "user_reply": reply}


def message_reference(message_ref, request_id):
    ref = message_ref if message_ref is not None else f"chat-reply/{request_id}"
    if not isinstance(ref, str) or not ref.strip():
        raise StudioError("INPUT_INVALID", "對話訊息參照無效。")
    return ref


def record_chat_reply(
    studio,
    project_id,
    expected_revision,
    request_id,
    domain,
    question_id,
    user_text,
    *,
    response_kind="answer",
    message_ref=None,
):
    """Trust the desktop host to relay a distinct user turn, retaining its actual words."""
    check_user_text(user_text)
    ref = message_reference(message_ref, request_id)
    with studio.repository.transaction():
        state = reply_state(studio, project_id, expected_revision)
        answer = chat_answer(state, request_id, user_text, ref, domain, question_id, response_kind)
        return studio.interviews.answer(project_id, expected_revision, request_id, domain, **answer)


def record_chat_replies(
    studio, project_id, expected_revision, request_id, user_text, replies, *, message_ref=None
):
    check_user_text(user_text)
    ref = message_reference(message_ref, request_id)
    if not isinstance(replies, list) or not 1 <= len(replies) <= len(DOMAINS):
        raise StudioError("INPUT_INVALID", "請提供本次實際回答的問題。")
    by_domain = {}
    for reply in replies:
        if (
            not isinstance(reply, dict)
            or set(reply) - {"domain", "question_id", "answer_text", "response_kind"}
            or not isinstance(reply.get("domain"), str)
            or reply["domain"] not in DOMAINS
            or reply["domain"] in by_domain
            or not reply.get("question_id")
        ):
            raise StudioError("INPUT_INVALID", "每個領域須有唯一且有效的問題識別碼。")
        by_domain[reply["domain"]] = reply
    with studio.repository.transaction():
        state = reply_state(studio, project_id, expected_revision)
        answers = []
        for domain in DOMAINS:
            if domain not in by_domain:
                continue
            answer = chat_answer(state, request_id, user_text, ref, **by_domain[domain])
            data = studio.interviews.answer_data(expected_revision, request_id, domain, **answer)
            answers.append((data, answer.get("user_reply")))
        return studio.interviews._save_answers(
            project_id,
            expected_revision,
            request_id,
            {
                "action": "reply_batch",
                "user_text": user_text,
                "message_ref": ref,
                "replies": [by_domain[key] for key in DOMAINS if key in by_domain],
            },
            answers,
        )


def preview(workspace, relative):
    from ..ingestion.security import reject_links, safe_relative

    name = safe_relative(relative)
    root = Path(workspace).resolve()
    path = root / name
    for parent in (path, *path.parents):
        if parent == root:
            break
        reject_links(parent)
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise StudioError("INPUT_INVALID", "附件不存在或位於工作區外。")
    local = {"path": name, "local_uri": path.as_uri(), "bytes": path.stat().st_size}
    try:
        from ..ingestion.models import ImportLimits
        from ..ingestion.preview import preview_file
        from ..ingestion.security import read_file

        limits = ImportLimits()
        if path.stat().st_size > limits.max_file_bytes:
            return {
                **local,
                "local_uri": path.parent.as_uri(),
                "kind": "metadata",
                "text": "附件超過內嵌預覽大小，已顯示檔名與大小，可在本機資料夾定位。",
            }
        suffix = path.suffix.lower()
        if suffix in {".mp3", ".wav", ".mp4", ".webm", ".mov"}:
            return {**local, "kind": "local", "text": "在本機播放器開啟原檔。"}
        return {
            **local,
            **asdict(preview_file(name, read_file(path, root, limits.max_file_bytes), limits)),
        }
    except (ImportError, StudioError) as exc:
        return {
            **local,
            "local_uri": path.parent.as_uri(),
            "kind": "metadata",
            "text": "無法內嵌此格式，已顯示檔名與大小。連結開啟所在資料夾。",
            "reason": getattr(exc, "code", "DECODER_UNAVAILABLE"),
        }


def dispatch(studio, workspace, action, params, *, runtime_python=None):
    """Ordinary transport data can propose an answer, never attest its author."""
    p = dict(params)
    if action == "create":
        return studio.interviews.create(**p)
    if action == "list":
        return [asdict(project) for project in studio.projects.list(**p)]
    if action == "preview":
        return preview(workspace, **p)
    if action == "qualify_pattern":
        from ..patterns.qualification import qualify

        return qualify(studio, **p, runtime_python=runtime_python)
    if action == "get_pattern_trial":
        return studio.tools.packs.trial(**p)
    if action == "generalize_patterns":
        from ..patterns.generalizer import generalize

        refs = p.pop("pattern_refs")
        if not isinstance(refs, list) or not 2 <= len(refs) <= 20:
            raise StudioError("INPUT_INVALID", "請提供 2–20 個來源 Pattern 版本。")
        sources = [
            studio.tools.packs.get_asset("pattern", ref).model.model_dump(mode="json")
            for ref in refs
        ]
        return generalize(sources, **p)
    if action in {"recommend_patterns", "decide_pattern", "inspect_pattern", "configure_patterns"}:
        from ..patterns import trigger

        handler = {
            "recommend_patterns": trigger.propose,
            "decide_pattern": trigger.decide,
            "inspect_pattern": trigger.inspect,
            "configure_patterns": trigger.configure,
        }[action]
        return handler(studio, **p)
    if action in {
        "propose_pattern_improvement",
        "compare_pattern_improvement",
        "get_pattern_improvement",
    }:
        from ..patterns import evolution

        if action == "get_pattern_improvement":
            return evolution.get(studio, **p)
        evolution.require_runtime_evidence(p)
        if action == "propose_pattern_improvement":
            return evolution.propose(studio, **p)
        return evolution.record_comparison(studio, **p)
    pid = p.pop("project_id")
    if action == "finalize_workflow_design":
        from .design_review import finalize

        return finalize(studio, pid, **p)
    if action == "compose_pattern":
        from ..patterns.composer import compose_project

        return compose_project(studio, pid, **p)
    if action in {"extract_pattern", "get_extraction", "export_extraction"}:
        from ..patterns import extractor

        if action == "extract_pattern":
            return extractor.save_extraction(studio, pid, p.pop("source"), p)
        if action == "get_extraction":
            return extractor.get_extraction(studio, pid, p["extraction_id"])
        return extractor.export_extraction(studio, workspace, pid, p["extraction_id"])
    if action == "source":
        return studio.sources.get(pid, p["source_id"]).manifest
    if action in {"prepare_blueprint", "export_blueprint", "get_blueprint"}:
        from . import blueprints

        if action == "prepare_blueprint":
            if {"_pattern_compositions", "_pattern_rehearsal"} & p.keys():
                raise StudioError("INPUT_INVALID", "請透過已確認的 Pattern 合成入口建立來源證據。")
            return blueprints.prepare(studio, pid, **p)
        if action == "export_blueprint":
            return blueprints.export(studio, workspace, pid, **p)
        for state in reversed(studio.interviews.history(pid)):
            revision = state.get("blueprint_revision")
            if revision and revision["sha256"] == p["sha256"]:
                return blueprints.deliver(studio, workspace, revision, pid)
        raise StudioError("REVISION_NOT_FOUND", "找不到此專案的藍圖版本。")
    if action == "attach":
        src = studio.sources.import_path(pid, p["expected_revision"], p["path"])
        return {"source_id": src.source_id, "version": src.version, "preview": src.manifest}
    if action in {"get", "status"}:
        try:
            return studio.interviews.get(pid)
        except StudioError as exc:
            if exc.code != "INTERVIEW_NOT_FOUND":
                raise
            project = studio.projects.get(pid)
            return {
                "project_id": pid,
                "name": project.name,
                "phase": "not_started",
                "revision": project.version,
                "next_actions": ["start"],
            }
    if action == "history":
        return studio.interviews.history(pid)
    if action == "next":
        question = next_question(studio.interviews.get(pid), **p)
        question["project_name"] = studio.projects.get(pid).name
        return question
    if action == "next_batch":
        questions = next_questions(studio.interviews.get(pid), **p)
        questions["project_name"] = studio.projects.get(pid).name
        return questions
    rev = p.pop("expected_revision")
    if action == "reply":
        return record_chat_reply(studio, pid, rev, **p)
    if action == "reply_batch":
        return record_chat_replies(studio, pid, rev, **p)
    if action == "start":
        return studio.interviews.start(pid, rev)
    if action in {"archive", "restore", "update"}:
        getattr(studio.projects, action)(pid, rev, **p)
        return studio.interviews.get(pid)
    request = p.pop("request_id")
    if action in {"pause", "resume"}:
        return studio.interviews.pause(pid, rev, request, resume=action == "resume")
    if action in {"answer", "revise", "skip", "reject"}:
        if "user_reply" in p or p.get("status") == "confirmed":
            raise StudioError(
                "USER_CONFIRMATION_REQUIRED", "請在桌面對話顯示問題後，轉交使用者的實際回覆。"
            )
        if action in {"skip", "reject"}:
            p.update(status="blocked", value=p.get("value") or "待使用者補充")
        return studio.interviews.answer(pid, rev, request, **p)
    raise StudioError("INPUT_INVALID", "未知的訪談操作。")
