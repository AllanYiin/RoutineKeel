"""Browse immutable artifacts and record current review decisions separately."""

import json
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText


def open_reviews(app):
    model = app.model
    project = model.project()
    editable = project.status == "active" and not app.has_unsaved_changes()
    window = tk.Toplevel(app.root)
    app.prepare_dialog(window)
    window.title("建置、驗收與審核")
    window.geometry(
        f"{min(1080, app.root.winfo_screenwidth() - 80)}x{min(760, app.root.winfo_screenheight() - 100)}"
    )
    window.columnconfigure(0, weight=1)
    window.rowconfigure(2, weight=1)
    ttk.Label(
        window,
        text="技術試做與人工審核分開記錄。退回不改寫舊報告；封存不刪除檔案，也不是取消已開始的試做。",
        padding=12,
    ).grid(row=0, column=0, sticky="ew")
    listing = ttk.Treeview(
        window, columns=("created", "build", "test", "review"), show="headings", height=5
    )
    for key, title, width in (
        ("created", "建立時間", 230),
        ("build", "建置", 170),
        ("test", "技術結果", 140),
        ("review", "審核狀態", 160),
    ):
        listing.heading(key, text=title)
        listing.column(key, width=width)
    listing.grid(row=1, column=0, sticky="ew", padx=12)
    tabs = ttk.Notebook(window)
    tabs.grid(row=2, column=0, sticky="nsew", padx=12, pady=8)
    overview, reports, files, events = [ttk.Frame(tabs, padding=8) for _ in range(4)]
    for frame, title in (
        (overview, "版本與審核理由"),
        (reports, "驗收報告"),
        (files, "程式與文件"),
        (events, "保存的工作事件"),
    ):
        tabs.add(frame, text=title)

    def text_area(parent):
        widget = ScrolledText(
            parent, width=1, height=5, wrap="word", state="disabled", font=app.analysis.cget("font")
        )
        widget.pack(fill="both", expand=True)
        return widget

    summary = text_area(overview)
    report_choice = ttk.Combobox(reports, state="readonly")
    report_choice.pack(fill="x", pady=(0, 6))
    report_text = text_area(reports)
    file_choice = ttk.Combobox(files, state="readonly")
    file_choice.pack(fill="x", pady=(0, 6))
    file_text = text_area(files)
    event_text = text_area(events)
    state = {
        "items": {},
        "selected": None,
        "details": None,
        "version": project.version,
        "cursor": 0,
    }
    actor, reason = tk.StringVar(value=app.actor_var.get()), tk.StringVar()
    footer = ttk.Frame(window, padding=12)
    footer.grid(row=3, column=0, sticky="ew")
    ttk.Label(footer, text="決策者").grid(row=0, column=0)
    ttk.Entry(footer, textvariable=actor, width=18).grid(row=0, column=1, sticky="ew", padx=6)
    ttk.Label(footer, text="理由").grid(row=0, column=2)
    ttk.Entry(footer, textvariable=reason).grid(row=0, column=3, sticky="ew", padx=6)
    footer.columnconfigure(3, weight=1)
    approval_choice = ttk.Combobox(footer, state="readonly", width=45)
    approval_choice.grid(row=1, column=0, columnspan=4, sticky="ew", pady=6)
    actions = ttk.Frame(footer)
    actions.grid(row=2, column=0, columnspan=4, sticky="ew")
    buttons = {}

    def enable(*_args):
        item = state["details"]
        ready = bool(editable and item and actor.get().strip() and reason.get().strip())
        for action, button in buttons.items():
            allowed = ready
            if item:
                if action == "restore":
                    allowed = ready and item["review"]["archived"]
                elif action == "revoke":
                    allowed = ready and bool(approval_choice.get())
                elif action == "archive":
                    allowed = (
                        ready
                        and not item["review"]["archived"]
                        and not item["release_references"]
                        and not any(a["revoked_at"] is None for a in item["approvals"])
                    )
                elif action == "submit":
                    latest = item["reports"][-1] if item["reports"] else None
                    allowed = bool(
                        ready
                        and not item["review"]["archived"]
                        and item["build"]["status"] == "verified"
                        and latest
                        and latest["status"] == "passed"
                        and (
                            not item["review"]["returned"]
                            or latest.get("review_sequence", 0)
                            >= item["review"]["returned_sequence"]
                        )
                    )
                else:
                    allowed = ready and not item["review"]["archived"]
            button.configure(state="normal" if allowed else "disabled")
        load_button.configure(
            state="normal" if editable and item and not item["review"]["archived"] else "disabled"
        )

    def report_selected(_event=None):
        item = state["details"]
        if item and report_choice.current() >= 0:
            report = item["reports"][report_choice.current()]
            lines = [
                "試做通過" if report["status"] == "passed" else "試做未通過",
                report["created_at"],
                report.get("message", ""),
            ]
            for case in report["cases"]:
                lines.append(f"案例 {case['case_id']} 第 {case['version']} 版 · {case['status']}")
                if case.get("difference"):
                    lines.append(
                        case["difference"].get(
                            "text_diff", case["difference"].get("reason", "內容不同")
                        )
                    )
            lines += ["完整報告（保留原始證據）", json.dumps(report, ensure_ascii=False, indent=2)]
            app._set_text(report_text, "\n\n".join(lines))

    def file_selected(_event=None):
        if state["selected"] and file_choice.get():
            app.guard(
                lambda: app._set_text(
                    file_text, model.build_file(state["selected"], file_choice.get())
                )
            )

    def select(_event=None):
        if not listing.selection():
            return
        identity = listing.selection()[0]
        if identity == state["selected"] and state["details"] is not None:
            return
        if identity != state["selected"] and reason.get().strip():
            if not messagebox.askyesno(
                "尚未送出理由", "放棄尚未送出的審核理由並切換建置？", parent=window
            ):
                listing.selection_set(state["selected"])
                return
        state["selected"] = identity
        item = model.build_details(identity)
        state["details"] = item
        build = item["build"]
        lines = [
            f"建置：{build['build_id']}",
            f"流程版本：{build['revision_id']}",
            f"套件指紋：{build['package_hash']}",
        ]
        shared = item["review"].get("revision_review")
        if shared:
            lines.append(
                f"本流程版本最近審核：{shared['action']}\n{shared['actor']}：{shared['reason']}"
            )
        for record in item["history"]:
            lines.append(
                f"{record['created_at']} · {record['action']}\n{record['actor'] or 'API 未提供操作者'}：{record['reason'] or ''}"
            )
        for approval in item["approvals"]:
            lines.append(
                f"核准 {approval['approval_id']} · {approval['actor']}\n{approval['reason']}\n"
                + ("已撤回" if approval["revoked_at"] else "未撤回；發布時仍需通過現行檢查")
            )
        for release in item["release_references"]:
            lines.append(
                f"發布紀錄：{release['status']} · {release['target_root']}\n此建置受發布歷史保護，不能直接封存。"
            )
        app._set_text(summary, "\n\n".join(lines))
        report_choice.configure(
            values=[f"{r['created_at']} · {r['status']}" for r in item["reports"]]
        )
        if item["reports"]:
            report_choice.current(len(item["reports"]) - 1)
            report_selected()
        else:
            report_choice.set("")
            app._set_text(report_text, "此建置尚無驗收報告。")
        names = ["README.md", "PROVENANCE.md", "workflow.ir.json"]
        names += [f"handler_{key}.py" for key in build["mapping"].get("generated_handlers", {})]
        if build["mapping"].get("generated_handlers"):
            names.append("generated-handler-tests.json")
        file_choice.configure(values=names)
        file_choice.current(0)
        app._set_text(file_text, "選擇檔案後顯示本機文字預覽；不執行程式。")
        approvals = [a["approval_id"] for a in item["approvals"] if a["revoked_at"] is None]
        approval_choice.configure(values=approvals)
        approval_choice.set(approvals[-1] if approvals else "")
        reason.set("")
        enable()

    def refresh(identity=None):
        state["details"] = None
        state["version"] = model.project().version
        state["items"] = {
            r["build"]["build_id"]: r for r in model.build_history(include_archived=True)
        }
        listing.delete(*listing.get_children())
        for key, item in state["items"].items():
            review = (
                "已封存"
                if item["review"]["archived"]
                else "退回"
                if item["review"]["returned"]
                else "已核准"
                if any(a["revoked_at"] is None for a in item["approvals"])
                else "待核對"
            )
            listing.insert(
                "",
                "end",
                iid=key,
                values=(
                    item["build"]["created_at"],
                    key[-12:],
                    {"candidate": "未試做", "verified": "試做通過", "rejected": "試做失敗"}.get(
                        item["build"]["status"], item["build"]["status"]
                    ),
                    review,
                ),
            )
        chosen = identity if identity in state["items"] else next(reversed(state["items"]), None)
        if chosen:
            listing.selection_set(chosen)
            select()
        else:
            state["selected"] = None
            app._set_text(summary, "尚未建立候選包。保存的分析事件仍可在事件分頁查看。")
        enable()

    def act(action):
        item = state["details"]
        if item is None:
            return
        try:
            if not messagebox.askyesno(
                "確認審核操作",
                "記錄此操作與理由？退回會撤回同一流程版本的有效核准；已安裝流程仍需另外停用。",
                parent=window,
            ):
                return
            if action == "revoke":
                model.revoke_build_approval(approval_choice.get(), actor.get(), reason.get())
            else:
                model.review_build(
                    state["version"],
                    item["build"]["build_id"],
                    item["build"]["package_hash"],
                    item["review"]["sequence"],
                    action,
                    actor.get(),
                    reason.get(),
                )
            reason.set("")
            app.last_report = None
            app.reviewed.set(False)
            app.refresh_project()
            refresh(state["selected"])
        except Exception as exc:
            app.error(exc)

    def load():
        if state["selected"]:
            try:
                item = model.select_build(state["selected"])
                window.destroy()
                app.last_report = None
                app.reviewed.set(False)
                if item["reports"]:
                    app.show_report(item["reports"][-1])
                app.refresh_actions()
            except Exception as exc:
                app.error(exc)

    def next_events():
        rows = model.project_events(state["cursor"])
        if rows:
            state["cursor"] = rows[-1]["sequence"]
        app._set_text(
            event_text,
            "\n\n".join(
                f"#{r['sequence']} {r['event']}\n" + json.dumps(r, ensure_ascii=False)[:1500]
                for r in rows
            )
            or "已讀到最後一頁。",
        )

    ttk.Button(
        events,
        text="下一頁保存事件（每頁 50 筆，長文字節錄）",
        command=lambda: app.guard(next_events),
    ).pack(fill="x", pady=6)
    for action, label in (
        ("submit", "送審"),
        ("return", "退回"),
        ("revoke", "撤回選取核准"),
        ("archive", "封存"),
        ("restore", "恢復"),
    ):
        button = ttk.Button(actions, text=label, command=lambda action=action: act(action))
        button.pack(side="left", padx=(0, 6))
        buttons[action] = button
    load_button = ttk.Button(actions, text="載入目前流程的建置", command=load, state="disabled")
    load_button.pack(side="left", padx=6)

    def close():
        if not reason.get().strip() or messagebox.askyesno(
            "尚未送出理由", "放棄尚未送出的審核理由？", parent=window
        ):
            window.destroy()

    ttk.Button(actions, text="關閉", command=close).pack(side="right")
    window.protocol("WM_DELETE_WINDOW", close)
    window.bind("<Escape>", lambda _event: close())
    actor.trace_add("write", enable)
    reason.trace_add("write", enable)
    approval_choice.bind("<<ComboboxSelected>>", enable)
    listing.bind("<<TreeviewSelect>>", lambda event: app.guard(lambda: select(event)))
    report_choice.bind("<<ComboboxSelected>>", report_selected)
    file_choice.bind("<<ComboboxSelected>>", file_selected)
    refresh(model.build_id)
