"""Explicit, snapshot-bound reconciliation of an unsaved goal with external updates."""

import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from ..contracts import StudioError


def open_comparison(app):
    model = app.model
    latest = model.project()
    baseline = app.project_edits.base or latest
    values = (app.name_var.get(), app.goal_var.get(), app.success_var.get())
    window = tk.Toplevel(app.root)
    app.prepare_dialog(window)
    window.title("比較目標更新；未儲存內容保留")
    window.geometry(
        f"{min(1100, app.root.winfo_screenwidth() - 80)}x{min(650, app.root.winfo_screenheight() - 120)}"
    )
    heading = ttk.Label(
        window,
        text="核對後可繼續保留草稿、明確載入最新內容，或以草稿更新畫面中的版本。儲存前仍檢查版本，新的外部變更不會被覆蓋。",
        wraplength=900,
        padding=12,
    )
    heading.pack(fill="x")
    window.bind(
        "<Configure>",
        lambda event: (
            heading.configure(wraplength=max(200, window.winfo_width() - 32))
            if event.widget is window
            else None
        ),
    )
    panes = ttk.Panedwindow(window, orient="horizontal")
    panes.pack(fill="both", expand=True, padx=12, pady=8)

    def fields(project):
        return (
            project.name,
            project.goal.get("description", ""),
            "\n".join(project.goal.get("success_criteria", [])),
        )

    for title, data in (
        (f"開始編輯時 · v{baseline.version}", fields(baseline)),
        (
            f"目前保存 · v{latest.version} · "
            + {"active": "使用中", "archived": "已封存", "deleted": "已刪除"}[latest.status],
            fields(latest),
        ),
        ("我的未儲存內容", values),
    ):
        frame = ttk.Frame(panes)
        ttk.Label(frame, text=title, padding=6).pack(fill="x")
        view = ScrolledText(frame, width=25, wrap="word", font=app.analysis.cget("font"))
        view.pack(fill="both", expand=True)
        view.insert("1.0", f"專案名稱\n{data[0]}\n\n工作目標\n{data[1]}\n\n成功條件\n{data[2]}")
        view.configure(state="disabled")
        panes.add(frame, weight=1)

    def check_scope():
        if app.model is not model or model.project_id != latest.project_id:
            raise StudioError("REVISION_CONFLICT", "工作區或專案已切換，未處理舊視窗的草稿。")

    def save():
        check_scope()
        app.save_project_values(values, latest)
        window.destroy()

    def discard():
        check_scope()
        if model.project().version != latest.version:
            raise StudioError(
                "REVISION_CONFLICT", "保存內容又有更新，請關閉後重新比較；草稿仍保留。"
            )
        app.refresh_project(discard_edits=True)
        app.status.set("已依明確選擇捨棄本機草稿，載入保存內容。")
        window.destroy()

    actions = ttk.Frame(window, padding=12)
    actions.pack(fill="x")
    ttk.Button(
        actions,
        text="以草稿更新此版本",
        command=lambda: app.guard(save),
        state="normal" if latest.status == "active" else "disabled",
    ).pack(side="left")
    ttk.Button(actions, text="捨棄草稿，載入此版本", command=lambda: app.guard(discard)).pack(
        side="left", padx=8
    )
    ttk.Button(actions, text="保留草稿，關閉", command=window.destroy).pack(side="right")
