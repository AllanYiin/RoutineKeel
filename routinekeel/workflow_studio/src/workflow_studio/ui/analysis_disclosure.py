"""Review the frozen text and images before transmitting them to a model."""

import tkinter as tk
from tkinter import ttk

from .source_preview import SourcePreview


def open_disclosure(app, prepared):
    disclosure = app.model.analysis_disclosure(prepared)
    window = tk.Toplevel(app.root)
    app.prepare_dialog(window)
    window.title("確認模型資料範圍")
    window.geometry(
        f"{min(960, app.root.winfo_screenwidth() - 80)}x{min(730, app.root.winfo_screenheight() - 120)}"
    )
    summary = f"目的端：{prepared.destination}\n模型：{prepared.model}\n將傳送下列完整來源、工作目標、已允許的工具說明及相關的本機關係比對／決策；同一範圍內最多再修正 {prepared.repair_limit} 輪。\n每次先預留約 USD {disclosure['reserved_micro_usd'] / 1_000_000:.4f}；專案設計期預算 USD {app.model.budget.budget_micro_usd / 1_000_000:.2f}。"
    if any(s.get("kind") == "image" for s in disclosure["sources"]):
        summary += f"\n圖片保持完整解析度、校正方向並移除額外 metadata。每張圖預留 {disclosure['image_tokens']:,} 輸入 Tokens；請確認模型支援看圖及計價方式，預留不等於服務帳單上限。"
    heading = ttk.Label(
        window, text=summary, wraplength=min(880, app.root.winfo_screenwidth() - 110), padding=12
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
    left = ttk.Frame(panes)
    listing = tk.Listbox(left, width=32, exportselection=False)
    scroll = ttk.Scrollbar(left, orient="vertical", command=listing.yview)
    listing.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    listing.pack(fill="both", expand=True)
    preview = SourcePreview(panes, font=app.analysis.cget("font"))
    panes.add(left, weight=2)
    panes.add(preview, weight=5)
    for item in disclosure["sources"]:
        transmission = item.get("transmission")
        kind = (
            f"圖片 {transmission['width']}×{transmission['height']}"
            if transmission
            else "文字／Mermaid"
        )
        listing.insert("end", f"{item['path']} · v{item['source_version']} · {kind}")

    def show(*_):
        if listing.curselection():
            asset = app.model.analysis_asset(prepared, listing.curselection()[0])
            preview.show(asset["name"], asset["content"], asset["preview"])

    def send():
        window.destroy()
        app.start_analysis(prepared)

    controls = ttk.Frame(window, padding=12)
    controls.pack(fill="x")
    ttk.Button(controls, text="取消", command=window.destroy).pack(side="right")
    ttk.Button(controls, text="確認範圍並開始分析", command=send, style="Primary.TButton").pack(
        side="right", padx=8
    )
    listing.bind("<<ListboxSelect>>", lambda event: app.guard(lambda: show(event)))
    if disclosure["sources"]:
        listing.selection_set(0)
        show()
