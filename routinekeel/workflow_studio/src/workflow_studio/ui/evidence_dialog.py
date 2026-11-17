"""Rules and human decisions, presented separately from executable node settings."""

import json
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from ..contracts import StudioError
from .source_preview import SourcePreview
from .widgets import BoundText

LABELS = {"直接可見": "observed", "推論／待確認": "inferred", "有矛盾": "conflicted"}


def open_evidence(app):
    model, project, revision = app.model, app.model.project(), app.model.revision()
    evidence = revision.ir["evidence"]
    sources = model.evidence_sources()
    source_map = {
        f"{s['name']} / {s['path']} · 第 {s['version']} 版 · {s['source_id'][-6:]}": s
        for s in sources
    }
    nodes = revision.ir["nodes"]
    window = tk.Toplevel(app.root)
    app.prepare_dialog(window)
    window.title("業務規則與證據確認")
    window.geometry(
        f"{min(1100, app.root.winfo_screenwidth() - 80)}x{min(760, app.root.winfo_screenheight() - 100)}"
    )
    window.columnconfigure(1, weight=1)
    window.columnconfigure(2, weight=1)
    window.rowconfigure(1, weight=1)
    ttk.Label(
        window,
        text="規則說明記錄業務判斷；修改說明不會自動改寫處理算法。人工確認也不代替試做與發布核准。",
        padding=12,
    ).grid(row=0, column=0, columnspan=3, sticky="w")
    left = ttk.Frame(window, padding=(12, 0, 6, 0))
    left.grid(row=1, column=0, sticky="nsew")
    listing = tk.Listbox(left, width=23, exportselection=False)
    listing.pack(fill="both", expand=True)
    for item in evidence:
        listing.insert(
            "end",
            item["id"] + (" · 已人工確認" if item["confidence_label"] == "human_confirmed" else ""),
        )
    fields = ttk.Frame(window, padding=6)
    fields.grid(row=1, column=1, sticky="nsew")
    fields.columnconfigure(0, weight=1)
    identity, source_choice, locator, label, claim = [tk.StringVar() for _ in range(5)]
    ttk.Label(fields, textvariable=identity).grid(row=0, column=0, sticky="w")
    ttk.Label(fields, text="依據來源（含版本）").grid(row=1, column=0, sticky="w", pady=(8, 3))
    source_widget = ttk.Combobox(
        fields, textvariable=source_choice, values=list(source_map), state="readonly"
    )
    source_widget.grid(row=2, column=0, sticky="ew")
    ttk.Label(fields, text="來源位置（檔名，可加 :行號）").grid(
        row=3, column=0, sticky="w", pady=(8, 3)
    )
    ttk.Entry(fields, textvariable=locator).grid(row=4, column=0, sticky="ew")
    ttk.Label(fields, text="規則說明").grid(row=5, column=0, sticky="w", pady=(8, 3))
    BoundText(fields, claim, width=1, height=5, wrap="word", font=app.analysis.cget("font")).grid(
        row=6, column=0, sticky="nsew"
    )
    fields.rowconfigure(6, weight=1)
    ttk.Combobox(fields, textvariable=label, values=list(LABELS), state="readonly").grid(
        row=7, column=0, sticky="ew", pady=6
    )
    ttk.Label(fields, text="使用此規則的步驟（可複選）").grid(row=8, column=0, sticky="w")
    binding = tk.Listbox(fields, height=4, selectmode="multiple", exportselection=False)
    binding.grid(row=9, column=0, sticky="ew", pady=6)
    for node in nodes:
        binding.insert("end", node["id"] + " · " + app.tool_titles.get(node["tool"], node["tool"]))
    actor, reason = tk.StringVar(value=app.actor_var.get()), tk.StringVar()
    ttk.Label(fields, text="確認／撤回者").grid(row=10, column=0, sticky="w")
    ttk.Entry(fields, textvariable=actor).grid(row=11, column=0, sticky="ew", pady=3)
    ttk.Label(fields, text="確認／撤回理由").grid(row=12, column=0, sticky="w")
    BoundText(fields, reason, width=1, height=3, wrap="word", font=app.analysis.cget("font")).grid(
        row=13, column=0, sticky="ew"
    )
    tabs = ttk.Notebook(window)
    tabs.grid(row=1, column=2, sticky="nsew", padx=(6, 12))
    preview = SourcePreview(tabs, font=app.analysis.cget("font"))
    history = ScrolledText(
        tabs, width=1, height=6, wrap="word", state="disabled", font=app.analysis.cget("font")
    )
    tabs.add(preview, text="來源內容")
    tabs.add(history, text="確認歷史")
    state = {"saved": None, "selected": None, "loading": False, "item": None}
    actions = {}
    status = tk.StringVar()
    ttk.Label(fields, textvariable=status, wraplength=320, foreground="#B45309").grid(
        row=14, column=0, sticky="ew", pady=6
    )

    def snapshot():
        return (
            source_choice.get(),
            locator.get(),
            label.get(),
            claim.get(),
            tuple(binding.curselection()),
        )

    def may_leave():
        return (
            state["saved"] is None
            or (snapshot() == state["saved"] and not reason.get().strip())
            or messagebox.askyesno("尚未儲存", "放棄尚未儲存的規則或決策理由？", parent=window)
        )

    def refresh_actions(*_args):
        if not actions:
            return
        item = state["item"]
        changed = snapshot() != state["saved"]
        confirmed = bool(item and item["confidence_label"] == "human_confirmed")
        ready = bool(item and not changed and actor.get().strip() and reason.get().strip())
        allowed = {
            "save": bool(claim.get().strip() and source_choice.get() and (changed or item is None)),
            "confirm": ready and not confirmed,
            "revoke": ready and confirmed,
            "remove": bool(item and not any(item["id"] in n["evidence_refs"] for n in nodes)),
        }
        for action, button in actions.items():
            button.configure(state="normal" if allowed[action] else "disabled")
        status.set(
            "此規則已人工確認；修改後儲存會建立待確認的新版本。"
            if confirmed
            else "請先保存規則，再填寫確認者與理由。"
        )

    def display_source(reset=False):
        source = source_map.get(source_choice.get())
        if source is None:
            return
        if reset:
            locator.set(source["path"])
        preview.show(
            source["path"],
            model.evidence_content(source["source_id"], source["version"], source["path"]),
            source["preview"],
        )

    def load(item=None):
        state["item"] = item
        if item:
            identity.set(item["id"])
            selected = next(
                (
                    k
                    for k, s in source_map.items()
                    if s["source_id"] == item["source_id"]
                    and s["version"] == item["source_version"]
                    and (
                        item["evidence_locator"] == s["path"]
                        or item["evidence_locator"].startswith(s["path"] + ":")
                    )
                ),
                "",
            )
            source_choice.set(selected)
            locator.set(item["evidence_locator"])
            claim.set(item["claim"])
            label.set(
                next(
                    (k for k, v in LABELS.items() if v == item["confidence_label"]), "推論／待確認"
                )
            )
        else:
            existing = {e["id"] for e in evidence}
            identity.set(next(f"rule_{i}" for i in range(1, 1000) if f"rule_{i}" not in existing))
            source_choice.set(next(iter(source_map), ""))
            locator.set(source_map[source_choice.get()]["path"] if source_choice.get() else "")
            claim.set("")
            label.set("推論／待確認")
        binding.selection_clear(0, "end")
        for i, node in enumerate(nodes):
            if item and item["id"] in node["evidence_refs"]:
                binding.selection_set(i)
        reason.set("")
        display_source()
        records = [r for r in model.evidence_decisions() if r["evidence_id"] == identity.get()]
        lines = []
        for record in records:
            basis = json.loads(record["basis_json"])
            lines.append(
                f"{record['created_at']} · {'人工確認' if record['action'] == 'confirm' else '撤回確認'}\n"
                f"{record['actor']}：{record['reason']}\n規則：{basis['claim']}\n來源：{basis['evidence_locator']}（第 {basis['source_version']} 版）"
            )
        app._set_text(history, "\n\n".join(lines) or "此規則尚無人工決策紀錄。")
        state["saved"] = snapshot()
        refresh_actions()

    def select(_event=None):
        if not listing.curselection():
            return
        index = listing.curselection()[0]
        if index == state["selected"]:
            return
        if not may_leave():
            listing.selection_clear(0, "end")
            if state["selected"] is not None:
                listing.selection_set(state["selected"])
            return
        state["selected"] = index
        load(evidence[index])

    def new():
        if may_leave():
            state["selected"] = None
            listing.selection_clear(0, "end")
            load()

    def finish(action):
        try:
            if action == "save":
                source = source_map.get(source_choice.get())
                if not source:
                    raise StudioError("INPUT_INVALID", "請先選取來源。")
                item = {
                    "id": identity.get(),
                    "source_id": source["source_id"],
                    "source_version": source["version"],
                    "evidence_locator": locator.get(),
                    "claim": claim.get(),
                    "confidence_label": LABELS[label.get()],
                    "verified_by": None,
                }
                model.edit_evidence(
                    project.version, item, [nodes[i]["id"] for i in binding.curselection()]
                )
            elif action == "remove":
                if state["item"] is None:
                    return
                if not messagebox.askyesno(
                    "移除規則", "僅可移除未被步驟引用的規則；歷史與決策紀錄保留。", parent=window
                ):
                    return
                model.remove_evidence(project.version, identity.get())
            else:
                if state["item"] is None or snapshot() != state["saved"]:
                    raise StudioError("INPUT_INVALID", "請先保存規則內容，再對已保存版本作出確認。")
                prompt = (
                    "撤回會使相同規則內容的先前確認失效；舊報告保留，既有安裝包不會自動移除。"
                    if action == "revoke"
                    else "確認目前已保存的規則與來源內容相符？確認會綁定內容指紋，仍需獨立試做。"
                )
                if not messagebox.askyesno("確認決策範圍", prompt, parent=window):
                    return
                model.decide_evidence(
                    project.version,
                    identity.get(),
                    actor.get(),
                    reason.get(),
                    revoke=action == "revoke",
                )
            window.destroy()
            app.last_report = None
            app.reviewed.set(False)
            app.refresh_project()
        except Exception as exc:
            app.error(exc)

    def close():
        if may_leave():
            window.destroy()

    ttk.Button(left, text="新增規則說明", command=new).pack(fill="x", pady=8)
    footer = ttk.Frame(window, padding=12)
    footer.grid(row=2, column=0, columnspan=3, sticky="ew")
    for text, action in (
        ("儲存規則新版", "save"),
        ("人工確認", "confirm"),
        ("撤回確認", "revoke"),
        ("移除未引用規則", "remove"),
    ):
        button = ttk.Button(footer, text=text, command=lambda action=action: finish(action))
        button.pack(side="left", padx=(0, 6))
        actions[action] = button
    ttk.Button(footer, text="關閉", command=close).pack(side="right")
    listing.bind("<<ListboxSelect>>", lambda event: app.guard(lambda: select(event)))
    source_widget.bind(
        "<<ComboboxSelected>>", lambda _event: app.guard(lambda: display_source(True))
    )
    for variable in (source_choice, locator, label, claim, actor, reason):
        variable.trace_add("write", refresh_actions)
    binding.bind("<<ListboxSelect>>", refresh_actions)
    window.protocol("WM_DELETE_WINDOW", close)
    window.bind("<Escape>", lambda _event: close())
    if evidence:
        listing.selection_set(0)
        select()
    else:
        load()
    listing.focus_set()
