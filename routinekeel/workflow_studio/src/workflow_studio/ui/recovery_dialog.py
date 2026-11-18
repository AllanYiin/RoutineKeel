import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from ..contracts import StudioError


def open_recovery(app):
    rows = [r for r in app.model.release_history() if r["status"] == "prepared"]
    window = tk.Toplevel(app.root)
    app.prepare_dialog(window)
    window.title("核對未完成發布準備")
    ttk.Label(
        window,
        text="先核對選取工作區中的實際檔案與指標，再決定是否補齊紀錄或結束準備。\n不刪除檔案、不執行工作流，也不自動重新核准。",
        padding=12,
    ).pack(anchor="w")
    listing = tk.Listbox(window, width=95, height=10, exportselection=False)
    listing.pack(fill="both", expand=True, padx=12, pady=8)
    for row in rows:
        listing.insert(
            "end", f"{row['created_at']} · {row['release_id'][-8:]} · {row['target_root']}"
        )

    def inspect():
        if not listing.curselection():
            return
        identity = rows[listing.curselection()[0]]["release_id"]
        window.destroy()
        app.status.set("正在取得發布鎖並核對實際套件……")
        app._background(
            lambda: app.model.inspect_release(identity),
            lambda observation: show_observation(app, observation),
        )

    controls = ttk.Frame(window, padding=12)
    controls.pack(fill="x")
    ttk.Button(
        controls, text="核對選取的準備", command=inspect, state="normal" if rows else "disabled"
    ).pack(side="left")
    ttk.Button(controls, text="關閉", command=window.destroy).pack(side="right")
    if not rows:
        listing.insert("end", "目前沒有未完成的發布準備紀錄。")


def show_observation(app, observed):
    window = tk.Toplevel(app.root)
    app.prepare_dialog(window)
    window.title("發布核對結果")
    outcome = observed["outcome"]
    action = "補齊已生效的發布紀錄" if outcome == "applied" else "結束未生效準備"
    text = ScrolledText(window, width=85, height=13, wrap="word", font=app.analysis.cget("font"))
    text.pack(fill="both", expand=True, padx=12, pady=12)
    text.insert(
        "1.0",
        f"工作區：{observed['target_root']}\n工作流：{observed['workflow_id']}\n\n{observed['explanation']}\n\n"
        "套用前會重新核對相同狀態。檔案、成果與舊版本均保留；結束準備不會撤回其他核准。\n"
        "如需封存建置，仍須先撤回有效核准。未知檔案不會被移除或覆寫。",
    )
    text.configure(state="disabled")
    if observed["approval_unavailable"] or observed["review_blocked"]:
        text.configure(state="normal")
        text.insert(
            "end",
            "\n\n注意：目前核准或審核已失效。補齊實際狀態不會恢復核准；已安裝版本請另行核對或停用。",
        )
        text.configure(state="disabled")
    actor, reason = tk.StringVar(value=app.actor_var.get()), tk.StringVar()
    for label, variable in (("操作者", actor), ("核對理由", reason)):
        row = ttk.Frame(window, padding=(12, 4))
        row.pack(fill="x")
        ttk.Label(row, text=label, width=12).pack(side="left")
        ttk.Entry(row, textvariable=variable).pack(fill="x", expand=True)

    def apply():
        if not actor.get().strip() or not reason.get().strip():
            app.error(StudioError("INPUT_INVALID", "請填操作者及核對理由。"))
            return
        reviewer, explanation = actor.get(), reason.get()
        window.destroy()
        app._background(
            lambda: app.model.reconcile_release(
                observed["release_id"], observed["observation_hash"], reviewer, explanation
            ),
            lambda result: app.status.set(
                "已補齊發布紀錄，檔案未變更。"
                if result["status"] != "abandoned"
                else "已結束未生效準備並保留檔案；可回建置審核處理核准與封存。"
            ),
        )

    controls = ttk.Frame(window, padding=12)
    controls.pack(fill="x")
    ttk.Button(
        controls,
        text=action,
        command=apply,
        state="normal" if outcome in {"applied", "not_applied"} else "disabled",
    ).pack(side="left")
    ttk.Button(controls, text="關閉", command=window.destroy).pack(side="right")
