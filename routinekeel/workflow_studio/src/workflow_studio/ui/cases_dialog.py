"""Case edits are local form drafts until the complete new version commits."""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .source_preview import SourcePreview

RULES = {"完全相同（包含排版）": "exact_bytes", "JSON 內容相同（忽略排版）": "json_equal"}


def open_cases(app):
    model = app.model
    project = model.project()
    cases = model.cases(include_archived=True)
    revision = model.revision()
    outputs = list(revision.ir.get("outputs", {})) if revision else []
    window = tk.Toplevel(app.root)
    app.prepare_dialog(window)
    window.title("試做案例與歷史")
    window.geometry(
        f"{min(880, app.root.winfo_screenwidth() - 80)}x{min(700, app.root.winfo_screenheight() - 120)}"
    )
    window.columnconfigure(1, weight=1)
    window.rowconfigure(5, weight=1)
    list_frame = ttk.Frame(window)
    list_frame.grid(row=0, column=0, rowspan=6, sticky="nsew", padx=12, pady=12)
    listing = tk.Listbox(list_frame, width=30, exportselection=False)
    scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=listing.yview)
    scrollbar.pack(side="right", fill="y")
    listing.configure(yscrollcommand=scrollbar.set)
    listing.pack(side="left", fill="both", expand=True)
    for case in cases:
        listing.insert(
            "end",
            case["specification"]["name"] + (" · 已封存" if case["status"] == "archived" else ""),
        )
    heading = ttk.Frame(window)
    heading.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=(12, 6))
    ttk.Label(heading, text="以哪個歷史版本為基礎").pack(side="left")
    versions = ttk.Combobox(heading, state="readonly", width=18)
    versions.pack(side="right")
    name, output, rule = tk.StringVar(), tk.StringVar(), tk.StringVar()
    for row, label, variable in (
        (1, "案例名稱", name),
        (2, "對應交付物", output),
        (3, "比較規則", rule),
    ):
        field = ttk.Frame(window)
        field.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=4)
        ttk.Label(field, text=label, width=12).pack(side="left")
        widget = (
            ttk.Entry(field, textvariable=variable)
            if row == 1
            else ttk.Combobox(
                field,
                textvariable=variable,
                state="readonly",
                values=outputs if row == 2 else list(RULES),
            )
        )
        widget.pack(side="left", fill="x", expand=True)
    paths = {"input": None, "expected": None}
    file_row = ttk.Frame(window)
    file_row.grid(row=4, column=1, sticky="ew", padx=(0, 12), pady=6)
    previews = ttk.Notebook(window)
    previews.grid(row=5, column=1, sticky="nsew", padx=(0, 12))
    assets = {
        side: SourcePreview(previews, font=app.analysis.cget("font"))
        for side in ("input", "expected")
    }
    previews.add(assets["input"], text="此版本輸入")
    previews.add(assets["expected"], text="此版本正確成果")
    preview = ScrolledText(
        previews, wrap="word", state="disabled", width=1, font=app.analysis.cget("font")
    )
    previews.add(preview, text="待替換檔案")
    state = {"case": None, "version": None, "rows": [], "saved": (), "index": None}

    def snapshot():
        return name.get(), output.get(), rule.get(), paths["input"], paths["expected"]

    def may_leave():
        return (
            state["case"] is None
            or snapshot() == state["saved"]
            or messagebox.askyesno("尚未儲存", "放棄這份案例的未儲存修改？", parent=window)
        )

    def close():
        if may_leave():
            window.destroy()

    def load_version(_event=None):
        if not state["rows"]:
            return
        if not may_leave():
            versions.current(
                next(i for i, r in enumerate(state["rows"]) if r["version"] == state["version"])
            )
            return
        case = state["rows"][versions.current()]
        state["case"], state["version"] = case["case_id"], case["version"]
        spec = case["specification"]
        name.set(spec["name"])
        output.set(spec.get("output_name", outputs[0] if len(outputs) == 1 else ""))
        rule.set(next(label for label, key in RULES.items() if key == spec["comparison"]))
        paths.update(input=None, expected=None)
        for side, asset in model.case_assets(case["case_id"], case["version"]).items():
            assets[side].show(asset["name"], asset["content"], asset["preview"])
        app._set_text(preview, "目前未選取替換檔案。")
        previews.select(assets["input"])
        state["saved"] = snapshot()

    def select(_event=None):
        if not listing.curselection():
            return
        index = listing.curselection()[0]
        if index == state["index"]:
            return
        if not may_leave():
            listing.selection_clear(0, "end")
            listing.selection_set(state["index"])
            return
        state["case"] = None
        state["index"] = index
        state["rows"] = model.case_history(cases[index]["case_id"])
        versions.configure(
            values=[
                f"第 {r['version']} 版" + (" · 封存" if r["status"] == "archived" else "")
                for r in state["rows"]
            ]
        )
        versions.current(len(state["rows"]) - 1)
        load_version()

    def choose(side):
        if state["case"] is None:
            return
        path = filedialog.askopenfilename(
            title="選擇新的試做輸入" if side == "input" else "選擇新的正確成果", parent=window
        )
        if path:
            paths[side] = path
            app._set_text(
                preview,
                "已選取替換檔案（尚未匯入）：\n"
                + "\n".join(
                    f"{label}：{paths[key] or '保留此版本內容'}"
                    for key, label in (("input", "輸入"), ("expected", "正確成果"))
                )
                + "\n\n儲存時會檢查格式、內容指紋及比較規則；失敗不會改寫舊案例。",
            )
            previews.select(preview)

    for side, label in (("input", "替換輸入檔"), ("expected", "替換正確成果")):
        ttk.Button(file_row, text=label, command=lambda side=side: choose(side)).pack(
            side="left", padx=(0, 8)
        )

    def save(archive=False):
        if state["case"] is None:
            return
        try:
            if archive:
                if not messagebox.askyesno(
                    "封存案例", "將停止使用這個案例；舊驗收報告與內容仍保留。", parent=window
                ):
                    return
                model.archive_case(state["case"], project.version)
            else:
                model.update_case(
                    state["case"],
                    state["version"],
                    project.version,
                    name=name.get(),
                    comparison=RULES[rule.get()],
                    output_name=output.get() or None,
                    input_path=paths["input"],
                    expected_path=paths["expected"],
                )
            window.destroy()
            app.last_report = None
            app.reviewed.set(False)
            app.refresh_project()
        except Exception as exc:
            app.error(exc)

    controls = ttk.Frame(window)
    controls.grid(row=6, column=0, columnspan=2, sticky="ew", padx=12, pady=12)
    ttk.Button(controls, text="儲存為新版本（重新啟用）", command=save).pack(side="left")
    ttk.Button(controls, text="封存案例", command=lambda: save(True)).pack(side="left", padx=8)
    ttk.Button(controls, text="取消", command=close).pack(side="right")
    window.protocol("WM_DELETE_WINDOW", close)
    window.bind("<Escape>", lambda _event: close())
    listing.bind("<<ListboxSelect>>", select)
    versions.bind("<<ComboboxSelected>>", load_version)
    if cases:
        listing.selection_set(0)
        select()
    listing.focus_set()
