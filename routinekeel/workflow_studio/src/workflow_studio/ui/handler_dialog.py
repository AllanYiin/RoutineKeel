import tkinter as tk
from tkinter import ttk

from ..contracts import StudioError


def save_selected_handler(app):
    model, project = app.model, app.model.project()
    if (
        not app.last_report
        or app.last_report["status"] != "passed"
        or app.last_report["build_id"] != model.build_id
    ):
        raise StudioError("TEST_FAILED", "請先對目前版本完成試做。")
    node = next(n for n in model.revision().ir["nodes"] if n["id"] == app.selected_node)
    if "program" not in node["parameters"]:
        raise StudioError("INPUT_INVALID", "選取的步驟不是自訂資料處理。")
    window = tk.Toplevel(app.root)
    app.prepare_dialog(window)
    window.title("保存為跨專案重用工具")
    ttk.Label(
        window,
        text="保存此步驟的程式、預設參數與合成測試，供本工作區其他專案使用。\n請先檢查是否含私人樣本或秘密；原始來源與實際試做案例不會複製。\n新工具先列為待核准，之後仍需以各專案自己的案例驗收。",
        padding=12,
        wraplength=620,
    ).pack(anchor="w")
    values = {}
    defaults = {
        "tool_id": "saved." + node["id"].replace("-", "_") + "_" + project.project_id[-6:],
        "version": "1.0.0",
        "title": node["parameters"].get("purpose", "自訂處理"),
        "actor": app.actor_var.get(),
        "reason": "",
    }
    for key, label in (
        ("tool_id", "工具識別碼"),
        ("version", "版本"),
        ("title", "名稱"),
        ("actor", "保存者"),
        ("reason", "重用理由"),
    ):
        row = ttk.Frame(window, padding=(12, 4))
        row.pack(fill="x")
        ttk.Label(row, text=label, width=15).pack(side="left")
        variable = tk.StringVar(value=defaults[key])
        values[key] = variable
        ttk.Entry(row, textvariable=variable, width=55).pack(fill="x", expand=True)
    build_id, package_hash = model.build_id, app.last_report["package_hash"]

    def save():
        settings = {k: v.get() for k, v in values.items()}
        if not settings["actor"].strip() or not settings["reason"].strip():
            app.error(StudioError("INPUT_INVALID", "請填保存者與重用理由。"))
            return
        window.destroy()
        app.status.set("正在核對來源套件與獨立驗收，保存固定 Handler……")

        def done(item):
            app.tool_titles = model.tool_titles()
            app.status.set(
                f"已保存 {item['tool_id']}@{item['version']}；請到工具管理核對用途、參數與驗收依據後核准。"
            )

        app._background(
            lambda: model.save_handler_tool(
                project.version, build_id, package_hash, node["id"], **settings
            ),
            done,
        )

    buttons = ttk.Frame(window, padding=12)
    buttons.pack(fill="x")
    ttk.Button(buttons, text="保存待核准工具", command=save).pack(side="right")
    ttk.Button(buttons, text="取消", command=window.destroy).pack(side="right", padx=8)
