"""Short, text-first requests. The host streams model output as it arrives."""

from dataclasses import asdict
from pathlib import Path

from ..contracts import StudioError
from .interviews import DOMAINS

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
        "local_confirmation": {
            "operation": "confirm",
            "payload": {
                "project_id": state["project_id"],
                "domain": domain,
                "expected_revision": state["revision"],
            },
        },
        "free_text": True,
        "how_to_reply": "直接輸入你的規則；也可以指定前一題修正、暫停或先保留缺口。",
        "next_actions": ["answer", "revise", "skip", "pause", "status"],
    }


def stream_text(deltas):
    """Forward genuine host/provider deltas; aborted streams have no completion event."""
    for index, text in enumerate(deltas):
        if not isinstance(text, str):
            raise StudioError("INPUT_INVALID", "串流事件須提供文字片段。")
        yield {"event": "delta", "sequence": index, "text": text}
    yield {"event": "completed"}


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


def dispatch(studio, workspace, action, params):
    """Ordinary transport data can propose an answer, never attest its author."""
    p = dict(params)
    if action == "create":
        return studio.interviews.create(**p)
    if action == "list":
        return [asdict(project) for project in studio.projects.list(**p)]
    if action == "preview":
        return preview(workspace, **p)
    pid = p.pop("project_id")
    if action == "source":
        return studio.sources.get(pid, p["source_id"]).manifest
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
    rev = p.pop("expected_revision")
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
            raise StudioError("USER_CONFIRMATION_REQUIRED", "請由可信使用者回覆入口確認此版本。")
        if action in {"skip", "reject"}:
            p.update(status="blocked", value=p.get("value") or "待使用者補充")
        return studio.interviews.answer(pid, rev, request, **p)
    raise StudioError("INPUT_INVALID", "未知的訪談操作。")
