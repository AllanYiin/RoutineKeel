"""Technical management: immutable descriptions, explicit trust and audited status changes."""

import copy
import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from ..contracts import StudioError
from .node_settings import ScrollableForm, numeric
from .widgets import BoundText

STATUS = {"active": "啟用", "disabled": "停用", "archived": "封存"}
TRUST = {"builtin": "內建", "approved": "已核准", "untrusted": "待核准"}


def open_tools(app):
    model = app.model
    window = tk.Toplevel(app.root)
    app.prepare_dialog(window)
    window.title("技術管理：工具與版本")
    window.geometry(
        f"{min(1100, app.root.winfo_screenwidth() - 80)}x{min(780, app.root.winfo_screenheight() - 100)}"
    )
    window.rowconfigure(2, weight=1)
    window.columnconfigure(0, weight=1)
    ttk.Label(
        window,
        text="工具設定共用於此工作區所有專案。註冊與核准描述不會執行程式，也不會自動授予網路或外部操作權限。",
        padding=12,
    ).grid(row=0, column=0, sticky="ew")
    table = ttk.Treeview(
        window, columns=("tool", "title", "trust", "status", "available"), show="headings", height=6
    )
    for key, title, width in (
        ("tool", "工具與版本", 230),
        ("title", "名稱", 230),
        ("trust", "信任", 90),
        ("status", "狀態", 80),
        ("available", "目前工作台可用", 130),
    ):
        table.heading(key, text=title)
        table.column(key, width=width)
    table.grid(row=1, column=0, sticky="ew", padx=12)
    tabs = ttk.Notebook(window)
    tabs.grid(row=2, column=0, sticky="nsew", padx=12, pady=8)
    detail = ScrolledText(
        tabs, width=1, height=5, wrap="word", state="disabled", font=app.analysis.cget("font")
    )
    history = ScrolledText(
        tabs, width=1, height=5, wrap="word", state="disabled", font=app.analysis.cget("font")
    )
    form = ScrollableForm(tabs)
    tabs.add(detail, text="目前契約與限制")
    tabs.add(form, text="以選取版本另存新描述")
    tabs.add(history, text="治理與核准歷史")
    fields = {}
    for key, title, multiline in (
        ("tool_id", "工具識別碼", False),
        ("version", "新版本（例如 1.0.1）", False),
        ("title", "名稱", False),
        ("capabilities", "能力（每行一項）", True),
        ("permissions", "所需權限（每行一項）", True),
        ("test_evidence", "測試依據（每行一項）", True),
        ("memory_mb", "宣告記憶體需求 MB", False),
        ("disk_mb", "宣告磁碟需求 MB", False),
        ("cost_micro_usd", "宣告成本（微美元）", False),
    ):
        variable = tk.StringVar()
        fields[key] = variable
        row = ttk.Frame(form.body)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text=title, width=25).pack(side="left", anchor="n")
        widget = (
            BoundText(row, variable, width=1, height=2, wrap="word", font=app.analysis.cget("font"))
            if multiline
            else ttk.Entry(row, textvariable=variable)
        )
        widget.pack(fill="x", expand=True)
    platforms = {key: tk.BooleanVar() for key in ("windows", "macos", "linux")}
    platform_row = ttk.Frame(form.body)
    platform_row.pack(fill="x", pady=6)
    ttk.Label(platform_row, text="宣告支援平台").pack(side="left")
    for key, variable in platforms.items():
        ttk.Checkbutton(platform_row, text=key, variable=variable).pack(side="left", padx=6)
    network = tk.BooleanVar()
    ttk.Checkbutton(form.body, text="此工具需要網路", variable=network).pack(anchor="w", pady=6)
    ttk.Label(
        form.body,
        text="此表單保留選取版本的實作及輸入輸出契約；完整自訂描述可用 JSON 匯入。\n測試依據須由核准者自行核對；不會自動執行描述中的路徑。金鑰請使用專案設定。",
        wraplength=700,
    ).pack(anchor="w", pady=8)
    footer = ttk.Frame(window, padding=12)
    footer.grid(row=3, column=0, sticky="ew")
    actor, reason = tk.StringVar(value=app.actor_var.get()), tk.StringVar()
    ttk.Label(footer, text="決策者").grid(row=0, column=0, sticky="w")
    ttk.Entry(footer, textvariable=actor, width=24).grid(row=0, column=1, sticky="ew", padx=6)
    ttk.Label(footer, text="理由").grid(row=0, column=2, sticky="w")
    ttk.Entry(footer, textvariable=reason).grid(row=0, column=3, sticky="ew", padx=6)
    footer.columnconfigure(3, weight=1)
    controls = ttk.Frame(footer)
    controls.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(10, 0))
    state = {"records": {}, "key": None, "baseline": None, "template": None}
    buttons = {}

    def snapshot():
        return (
            {k: v.get() for k, v in fields.items()},
            {k: v.get() for k, v in platforms.items()},
            network.get(),
        )

    def dirty():
        return state["baseline"] is not None and snapshot() != state["baseline"]

    def may_leave():
        return not (dirty() or reason.get().strip()) or messagebox.askyesno(
            "尚未儲存", "放棄未儲存的工具描述或決策理由？", parent=window
        )

    def refresh_actions(*_args):
        item = state["records"].get(state["key"])
        ready = bool(item and actor.get().strip() and reason.get().strip() and not dirty())
        for action, button in buttons.items():
            enabled = ready
            if item:
                if action == "approve":
                    enabled = (
                        ready and item["trust_status"] == "untrusted" and item["status"] == "active"
                    )
                elif action == "revoke":
                    enabled = ready and item["trust_status"] == "approved"
                else:
                    enabled = ready and item["status"] != action
            button.configure(state="normal" if enabled else "disabled")

    def fill(item):
        tool = item["tool"].model_dump()
        state["template"] = tool
        for key in ("tool_id", "title"):
            fields[key].set(tool[key])
        major, minor, patch = map(int, tool["version"].split("."))
        fields["version"].set(f"{major}.{minor}.{patch + 1}")
        for key in ("capabilities", "permissions", "test_evidence"):
            fields[key].set("\n".join(tool[key]))
        for key in ("memory_mb", "disk_mb"):
            fields[key].set(tool["resources"][key])
        fields["cost_micro_usd"].set(tool["cost_micro_usd"])
        for key, variable in platforms.items():
            variable.set(key in tool["platforms"])
        network.set(tool["resources"]["network"])
        app._set_text(
            detail,
            f"{item['support_reason']}\n{item['policy_reason']}\n契約 SHA-256：{item['contract_hash']}\n\n"
            + json.dumps(tool, ensure_ascii=False, indent=2),
        )
        records = model.tool_history(item["tool_id"], item["version"])
        lines = [
            f"{e['created_at']} · {e['action']} · {e['actor'] or '系統／API（未提供操作者）'}\n{e['reason'] or ''}\n{e['payload_json']}"
            for e in records["events"]
        ]
        lines += [
            f"既有核准：{e['created_at']} · {e['actor']}\n{e['reason']}"
            for e in records["approvals"]
        ]
        app._set_text(history, "\n\n".join(lines) or "尚無治理紀錄。")
        reason.set("")
        state["baseline"] = snapshot()
        refresh_actions()

    def refresh(key=None):
        records = model.tool_records()
        state["records"] = {f"{r['tool_id']}@{r['version']}": r for r in records}
        table.delete(*table.get_children())
        for identity, item in state["records"].items():
            table.insert(
                "",
                "end",
                iid=identity,
                values=(
                    identity,
                    item["tool"].title,
                    TRUST[item["trust_status"]],
                    STATUS[item["status"]],
                    "可用" if item["available"] else "不可用",
                ),
            )
        selected = key if key in state["records"] else next(iter(state["records"]), None)
        state["key"] = selected
        if selected:
            table.selection_set(selected)
            table.see(selected)
            fill(state["records"][selected])

    def select(_event=None):
        if not table.selection():
            return
        key = table.selection()[0]
        if key == state["key"]:
            return
        if not may_leave():
            table.selection_set(state["key"])
            return
        state["key"] = key
        fill(state["records"][key])

    def changed_parent():
        app.tool_titles = model.tool_titles()
        app.last_report = None
        app.reviewed.set(False)
        if model.project_id:
            app.refresh_project()
        app.refresh_actions()

    def register(imported=None):
        try:
            if imported is None:
                if state["template"] is None:
                    return
                payload = copy.deepcopy(state["template"])
                for key in ("tool_id", "title", "version"):
                    payload[key] = fields[key].get().strip()
                for key in ("capabilities", "permissions", "test_evidence"):
                    payload[key] = [
                        line.strip() for line in fields[key].get().splitlines() if line.strip()
                    ]
                payload["platforms"] = [k for k, v in platforms.items() if v.get()]
                payload["cost_micro_usd"] = numeric(fields["cost_micro_usd"].get(), integer=True)
                payload["resources"] = {
                    "memory_mb": numeric(fields["memory_mb"].get(), integer=True),
                    "disk_mb": numeric(fields["disk_mb"].get(), integer=True),
                    "network": network.get(),
                }
            else:
                payload = imported
            reviewer = (
                {"actor": actor.get().strip(), "reason": reason.get().strip()}
                if actor.get().strip() and reason.get().strip()
                else {}
            )
            item = model.register_tool(payload, **reviewer)
            changed_parent()
            refresh(f"{item['tool_id']}@{item['version']}")
            tabs.select(detail)
        except Exception as exc:
            app.error(exc)

    def import_file():
        if not may_leave():
            return
        path = filedialog.askopenfilename(
            parent=window, title="匯入工具描述（不執行程式）", filetypes=[("JSON", "*.json")]
        )
        if path:
            try:
                register(model.read_tool_file(path))
            except Exception as exc:
                app.error(exc)

    def decide(action):
        try:
            if dirty():
                raise StudioError(
                    "INPUT_INVALID", "請先另存新版本或放棄表單修改，再審核已保存的描述。"
                )
            item = state["records"].get(state["key"])
            if item is None:
                return
            verb = {
                "approve": "核准描述",
                "revoke": "撤回人工核准",
                "active": "啟用",
                "disabled": "停用",
                "archived": "封存",
            }[action]
            if not messagebox.askyesno(
                "確認工具決策",
                f"{verb} {state['key']}？\n\n{item['support_reason']}\n\n此變更影響工作區內所有專案的後續編譯與發布。已安裝流程仍可執行，需另行停用發布版本。",
                parent=window,
            ):
                return
            model.decide_tool(item, action, actor.get(), reason.get())
            changed_parent()
            refresh(state["key"])
        except Exception as exc:
            app.error(exc)

    ttk.Button(controls, text="另存新描述", command=register).pack(side="left")
    ttk.Button(controls, text="匯入 JSON", command=import_file).pack(side="left", padx=5)
    for action, text in (
        ("approve", "核准描述"),
        ("revoke", "撤回核准"),
        ("active", "啟用"),
        ("disabled", "停用"),
        ("archived", "封存"),
    ):
        button = ttk.Button(controls, text=text, command=lambda action=action: decide(action))
        button.pack(side="left", padx=3)
        buttons[action] = button

    def close():
        if may_leave():
            window.destroy()

    ttk.Button(controls, text="關閉", command=close).pack(side="right")
    for variable in [*fields.values(), *platforms.values(), network, actor, reason]:
        variable.trace_add("write", refresh_actions)
    table.bind("<<TreeviewSelect>>", select)
    window.protocol("WM_DELETE_WINDOW", close)
    window.bind("<Escape>", lambda _event: close())
    refresh()
