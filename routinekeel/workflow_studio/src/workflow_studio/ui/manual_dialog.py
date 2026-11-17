import tkinter as tk
from tkinter import ttk

from ..contracts import StudioError


def open_manual(app):
    model, project = app.model, app.model.project()
    source_rows = model.evidence_sources()
    source_map = {
        f"{s['name']} / {s['path']} · 第 {s['version']} 版 · {s['source_id'][-6:]}": s
        for s in source_rows
    }
    tools = {
        tool["title"] + " · " + key: (key, tool)
        for key, tool in model.editor_tools().items()
        if tool["implementation"]
        in {"builtin.copy", "builtin.normalize", "builtin.json", "builtin.report"}
    }
    window = tk.Toplevel(app.root)
    app.prepare_dialog(window)
    window.title("手動建立第一個基礎流程")
    ttk.Label(
        window,
        text="選擇設計依據與第一個動作，建立後可新增步驟與分支。\n不會呼叫模型或執行流程；仍需用固定案例試做。",
        padding=12,
    ).pack(anchor="w")
    source, action, filename, format_var = (
        tk.StringVar(),
        tk.StringVar(),
        tk.StringVar(value="artifacts/result.bin"),
        tk.StringVar(value="未指定"),
    )
    widgets = {}
    for title, variable, options in (
        ("設計依據", source, list(source_map)),
        ("第一個動作", action, list(tools)),
        ("實際輸入格式（不是轉檔）", format_var, ["未指定", "txt", "csv", "json"]),
        ("成果檔名／相對位置", filename, None),
    ):
        row = ttk.Frame(window, padding=(12, 4))
        row.pack(fill="x")
        ttk.Label(row, text=title, width=23).pack(side="left")
        widget = (
            ttk.Combobox(row, textvariable=variable, values=options, state="readonly", width=45)
            if options
            else ttk.Entry(row, textvariable=variable, width=45)
        )
        widget.pack(fill="x", expand=True)
        widgets[title] = widget
    if source_map:
        source.set(next(iter(source_map)))
    if tools:
        action.set(next(iter(tools)))

    def action_changed(_event=None):
        if action.get() not in tools:
            return
        _, tool = tools[action.get()]
        required = tool["inputs"]["source"].get("format")
        if required:
            format_var.set(required)
        if filename.get().startswith("artifacts/result."):
            filename.set("artifacts/result." + (tool["outputs"]["result"].get("format") or "bin"))

    widgets["第一個動作"].bind("<<ComboboxSelected>>", action_changed)

    def save():
        try:
            if source.get() not in source_map or action.get() not in tools:
                raise StudioError("INPUT_INVALID", "請選取來源與動作。")
            selected = source_map[source.get()]
            key, tool = tools[action.get()]
            model.initialize_flow(
                project.version,
                selected["source_id"],
                selected["version"],
                selected["path"],
                key,
                output_path=filename.get(),
                input_format=None if format_var.get() == "未指定" else format_var.get(),
                parameters={},
            )
            window.destroy()
            app.last_report = None
            app.reviewed.set(False)
            app.refresh_project()
        except Exception as exc:
            app.error(exc)

    buttons = ttk.Frame(window, padding=12)
    buttons.pack(fill="x")
    ttk.Button(buttons, text="建立待試做流程", command=save).pack(side="right")
    ttk.Button(buttons, text="取消", command=window.destroy).pack(side="right", padx=8)
