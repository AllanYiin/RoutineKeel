"""History browsing never switches a published workflow or mutates a past snapshot."""

import json
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

STATUS = {
    "draft": "草稿",
    "ir_ready": "待試做",
    "needs_clarification": "待補規則",
    "rejected": "未通過",
    "verified": "試做通過",
    "approved": "已核准",
    "published": "已發布",
}
LABELS = {
    "nodes": "步驟",
    "goal": "工作目標",
    "description": "說明",
    "success_criteria": "成功條件",
    "parameters": "設定",
    "inputs": "輸入",
    "outputs": "成果",
    "depends_on": "前置步驟",
    "evidence": "證據",
    "revision": "流程版號",
    "program": "處理程式",
    "arguments": "處理參數",
}


def describe_diff(comparison):
    def value(item):
        text = json.dumps(item, ensure_ascii=False, indent=2)
        return text[:1600] + ("\n（內容截斷）" if len(text) > 1600 else "")

    lines = []
    for change in comparison["changes"]:
        path = " / ".join(LABELS.get(p, p) for p in change["path"])
        action = {"added": "新增", "removed": "移除", "changed": "修改"}[change["operation"]]
        lines.append(
            f"{action}：{path}\n選取版：{value(change['before'])}\n目前版：{value(change['after'])}"
        )
    if comparison["truncated"]:
        lines.append("變更多於預覽上限，僅顯示前 500 項。")
    return "\n\n".join(lines) or "兩版內容相同。"


def open_history(app):
    model = app.model
    project = model.project()
    rows = model.revision_history()
    window = tk.Toplevel(app.root)
    app.prepare_dialog(window)
    window.title("版本與差異")
    window.geometry(
        f"{min(900, app.root.winfo_screenwidth() - 80)}x{min(660, app.root.winfo_screenheight() - 120)}"
    )
    window.rowconfigure(1, weight=1)
    window.columnconfigure(1, weight=1)
    ttk.Label(window, text="選取歷史版本，與目前版本比較；續編會另存新版本。", padding=12).grid(
        row=0, column=0, columnspan=2, sticky="w"
    )
    list_frame = ttk.Frame(window)
    list_frame.grid(row=1, column=0, sticky="nsew", padx=(12, 6))
    listing = tk.Listbox(list_frame, width=30, exportselection=False)
    scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=listing.yview)
    scrollbar.pack(side="right", fill="y")
    listing.configure(yscrollcommand=scrollbar.set)
    listing.pack(side="left", fill="both", expand=True)
    details = ScrolledText(
        window, wrap="word", width=1, state="disabled", font=app.analysis.cget("font")
    )
    details.grid(row=1, column=1, sticky="nsew", padx=(0, 12))
    for index, revision in enumerate(rows):
        flags = " · 目前" if revision.revision_id == project.current_revision_id else ""
        flags += " · 已捨棄" if revision.discarded else ""
        listing.insert(
            "end", f"紀錄 {index + 1} · {STATUS.get(revision.status, revision.status)}{flags}"
        )
    restore = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        window, text="若目標不同，同時還原選取版本的工作目標與成功條件", variable=restore
    ).grid(row=2, column=0, columnspan=2, sticky="w", padx=12, pady=8)
    controls = ttk.Frame(window)
    controls.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 12))

    def selected():
        return rows[listing.curselection()[0]] if listing.curselection() else None

    def show(_event=None):
        revision = selected()
        if revision is None:
            return
        content = revision.ir
        goal = content.get("goal")
        goal = goal if isinstance(goal, dict) else {}
        criteria = goal.get("success_criteria", [])
        criteria = criteria if isinstance(criteria, list) else [criteria]
        summary = [
            f"建立時間：{revision.created_at}",
            f"狀態：{STATUS.get(revision.status, revision.status)}",
            f"內容指紋：{revision.ir_hash}",
            "工作目標：" + str(goal.get("description", "草稿尚未定義")),
            "成功條件：\n" + "\n".join(str(item) for item in criteria),
        ]
        if project.current_revision_id:
            summary += [
                "選取版 → 目前版的差異",
                describe_diff(
                    model.compare_revisions(revision.revision_id, project.current_revision_id)
                ),
            ]
        app._set_text(details, "\n\n".join(summary))

    def act(discard=False):
        revision = selected()
        if revision is None:
            return
        prompt = (
            "捨棄這份未發布草稿？歷史內容仍會保留。"
            if discard
            else "以選取版本建立新的工作版本？需重新試做與核准，正式發布版本不變。"
        )
        if not messagebox.askyesno("確認版本操作", prompt, parent=window):
            return
        try:
            if discard:
                model.discard_revision(revision.revision_id, project.version)
            else:
                model.reuse_revision(
                    revision.revision_id, project.version, restore_goal=restore.get()
                )
            window.destroy()
            app.last_report = None
            app.reviewed.set(False)
            app.refresh_project()
        except Exception as exc:
            app.error(exc)

    active = project.status == "active" and not app.has_unsaved_changes()
    if not active:
        ttk.Label(controls, text="唯讀：請先恢復專案或儲存目標。", foreground="#B45309").pack(
            side="top", anchor="w", pady=(0, 6)
        )
    ttk.Button(
        controls, text="從這版續編", command=lambda: act(), state="normal" if active else "disabled"
    ).pack(side="left")
    ttk.Button(
        controls,
        text="捨棄未發布草稿",
        command=lambda: act(True),
        state="normal" if active else "disabled",
    ).pack(side="left", padx=8)
    ttk.Button(controls, text="關閉", command=window.destroy).pack(side="right")
    listing.bind("<<ListboxSelect>>", show)
    if rows:
        index = next(
            (i for i, r in enumerate(rows) if r.revision_id == project.current_revision_id),
            len(rows) - 1,
        )
        listing.selection_set(index)
        show()
    else:
        app._set_text(details, "尚未保存流程草稿。")
    listing.focus_set()
