"""Versioned source annotations, kept separate from the imported file bytes."""

import tkinter as tk
from tkinter import ttk


def source_caption(source):
    caption = f"{source.name} · 第 {source.version} 版"
    description = " ".join(source.manifest.get("description", "").split())
    if description:
        summary = description[:240] + ("…（完整內容見來源說明）" if len(description) > 240 else "")
        caption += "\n說明：" + summary
    return caption


def open_source_description(app):
    source = app.single_source()
    expected_project = app.current_project
    window = tk.Toplevel(app.root)
    app.prepare_dialog(window)
    window.title("編輯來源說明")
    ttk.Label(window, text=f"{source.name} · 第 {source.version} 版", wraplength=620).pack(
        anchor="w", padx=14, pady=(12, 6)
    )
    ttk.Label(
        window,
        text="可記錄用途、來源背景或需要注意的限制（最多 8000 字）。\n儲存會新增來源版本，保留原檔與舊說明；已有流程需重新核對與試做。",
        wraplength=620,
    ).pack(anchor="w", padx=14, pady=(0, 8))
    editor = tk.Text(window, width=70, height=10, wrap="word", undo=True)
    editor.pack(fill="both", expand=True, padx=14, pady=6)
    editor.insert("1.0", source.manifest.get("description", ""))

    def save():
        try:
            app.model.describe_source(
                source.source_id, editor.get("1.0", "end-1c"), expected_project
            )
            window.destroy()
            app.refresh_project()
            app.focus_imported_source(source.source_id)
            app.status.set("來源說明已另存新版，原檔與歷史保留。已有流程請重新核對並試做。")
        except Exception as exc:
            app.error(exc)

    actions = ttk.Frame(window)
    actions.pack(fill="x", padx=14, pady=(4, 12))
    ttk.Button(actions, text="儲存說明", command=save).pack(side="left")
    ttk.Button(actions, text="取消", command=window.destroy).pack(side="left", padx=8)
    editor.focus_set()
    return window
