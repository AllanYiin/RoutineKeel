"""Review immutable folder evidence and explicitly choose relations and file roles."""

import json
import tkinter as tk
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from ..contracts import StudioError
from .source_preview import SourcePreview

ROLES = {
    "輸入": "input",
    "成果": "output",
    "中間產物": "intermediate",
    "日誌": "log",
    "未知": "unknown",
}
LABELS = {"inferred": "推論候選", "human_confirmed": "人工確認"}


def history_summary(graph):
    lines = ["待補證據"]
    lines.extend(f"• {item['path']}：{item['reason']}" for item in graph["unknowns"])
    if not graph["unknowns"]:
        lines.append("目前沒有標記為未知的檔案；仍需試做驗收。")
    lines.extend(["", "時間與日誌只是觀察，不證明歷史因果。"])
    for item in graph.get("time_observations", []):
        lines.append(
            f"• {item['from']} → {item['to']}："
            + ("輸入時間較早或相同" if item["input_before_output"] else "輸入時間較晚")
        )
    for item in graph.get("log_observations", []):
        lines.append(f"• 日誌 {item['path']} 提到：" + "、".join(item["referenced_files"]))
    lines.extend(["", "決策歷史（此證據圖版本）"])
    actions = {
        "confirm": "確認關係",
        "unconfirm": "改為待確認",
        "remove": "排除關係",
        "replace": "替換關係",
        "roles": "重新指定角色並比對",
    }
    for item in graph.get("decisions", []):
        lines.append(
            f"• {item['at']} · {item['actor']} · {actions.get(item['action'], item['action'])}\n  理由：{item.get('reason') or '舊紀錄未提供理由'}"
        )
        if "before" in item:
            edge = item["before"]
            lines.append(f"  {edge['from']} → {edge['to']} · {edge['tool']}")
        if "roles" in item:
            lines.extend(
                f"  {path}：{next(k for k, v in ROLES.items() if v == role)}"
                for path, role in item["roles"].items()
            )
    return "\n".join(lines)


def open_folder_review(app):
    model = app.model
    graphs = model.folder_graphs()
    names = {s.source_id: s.name for s in model.sources()}
    window = tk.Toplevel(app.root)
    app.prepare_dialog(window)
    window.title("案例關係、檔案角色與版本")
    window.geometry(
        f"{min(1000, app.root.winfo_screenwidth() - 80)}x{min(780, app.root.winfo_screenheight() - 120)}"
    )
    head = ttk.Frame(window, padding=12)
    head.pack(fill="x")
    graph_choice = ttk.Combobox(
        head,
        state="readonly",
        width=65,
        values=[
            f"{names.get(g['graph']['source_id'], '歷史來源')} · 比對 {i + 1} · v{g['version']}"
            + ("（封存）" if g["status"] != "active" else "")
            for i, g in enumerate(graphs)
        ],
    )
    graph_choice.pack(side="left", fill="x", expand=True)
    version_choice = ttk.Combobox(head, state="readonly", width=18)
    version_choice.pack(side="right", padx=(8, 0))
    status = tk.StringVar(value="先從成功案例找流程，產生本機比對證據。")
    ttk.Label(window, textvariable=status, wraplength=850, padding=(12, 0, 12, 8)).pack(fill="x")
    tabs = ttk.Notebook(window)
    tabs.pack(fill="both", expand=True, padx=12)
    files_tab, relations_tab = ttk.Frame(tabs), ttk.Frame(tabs)
    history_text = ScrolledText(tabs, wrap="word", state="disabled", font=app.analysis.cget("font"))
    tabs.add(files_tab, text="檔案角色與預覽")
    tabs.add(relations_tab, text="處理關係與依據")
    tabs.add(history_text, text="決策與未知事項")

    def tree(parent, columns):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        widget = ttk.Treeview(
            frame, columns=list(columns), show="headings", height=7, selectmode="browse"
        )
        for key, label in columns.items():
            widget.heading(key, text=label)
            widget.column(key, width=190 if key in {"path", "from", "to"} else 125, minwidth=60)
        vertical = ttk.Scrollbar(frame, orient="vertical", command=widget.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=widget.xview)
        widget.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        horizontal.pack(side="bottom", fill="x")
        vertical.pack(side="right", fill="y")
        widget.pack(fill="both", expand=True)
        return widget

    files = tree(files_tab, {"path": "檔案", "role": "角色", "basis": "角色依據"})
    file_controls = ttk.Frame(files_tab, padding=8)
    file_controls.pack(fill="x")
    role = ttk.Combobox(file_controls, values=list(ROLES), state="readonly", width=12)
    role.pack(side="left")
    relations = tree(
        relations_tab, {"from": "輸入", "to": "成果", "tool": "處理方式", "status": "判定"}
    )
    detail = ScrolledText(
        relations_tab, height=7, wrap="word", state="disabled", font=app.analysis.cget("font")
    )
    detail.pack(fill="both", expand=True, pady=8)
    alternative = ttk.Combobox(relations_tab, state="readonly")
    alternative.pack(fill="x", padx=8)
    relation_controls = ttk.Frame(relations_tab, padding=8)
    relation_controls.pack(fill="x")
    footer = ttk.Frame(window, padding=12)
    footer.pack(fill="x")
    actor, reason = tk.StringVar(value=app.actor_var.get()), tk.StringVar()
    for label, variable in (("決策者", actor), ("理由", reason)):
        row = ttk.Frame(footer)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text=label, width=9).pack(side="left")
        ttk.Entry(row, textvariable=variable).pack(fill="x", expand=True)
    controls = ttk.Frame(footer)
    controls.pack(fill="x", pady=(8, 0))
    state = {"snapshot": None, "data": None, "roles": {}, "alternatives": [], "busy": False}
    mutations = []

    def editable():
        snapshot, data = state["snapshot"], state["data"]
        return bool(
            snapshot
            and not state["busy"]
            and data["source_current"]
            and snapshot["version"] == data["history"][-1]["version"]
            and snapshot["status"] == "active"
        )

    def buttons():
        for button in mutations:
            button.configure(state="normal" if editable() else "disabled")
        role.configure(state="readonly" if editable() else "disabled")
        graph_choice.configure(state="disabled" if state["busy"] else "readonly")
        version_choice.configure(state="disabled" if state["busy"] else "readonly")

    def selected_edge():
        return (
            next(
                (
                    r
                    for r in state["snapshot"]["graph"]["relations"]
                    if relations.selection() and r["relation_id"] == relations.selection()[0]
                ),
                None,
            )
            if state["snapshot"]
            else None
        )

    def show_relation(*_):
        edge = selected_edge()
        state["alternatives"] = []
        alternative.set("")
        if edge:
            decision = edge.get("verified_by") or {}
            confirmation = (
                f"{decision['actor']} · {decision.get('at', '')}\n理由：{decision.get('reason') or '舊紀錄未提供理由'}"
                if decision
                else "尚未人工確認"
            )
            state["alternatives"] = [
                r
                for r in state["snapshot"]["graph"]["relations"]
                if r["relation_id"] != edge["relation_id"]
                and (r["from"], r["to"]) == (edge["from"], edge["to"])
            ]
            app._set_text(
                detail,
                f"{edge['from']} → {edge['to']}\n{edge['tool']}@{edge['tool_version']}\n參數：{json.dumps(edge['parameters'], ensure_ascii=False)}\n{edge['claim']}\n樣本重現：{'一致' if edge['sample_match'] else '不一致'}；不代表歷史因果或所有例外。\n確認：{confirmation}",
            )
        else:
            app._set_text(
                detail, "沒有選取關係。若缺少輸入或中間產物，請補充來源或調整角色後重新比對。"
            )
        alternative.configure(
            values=[
                f"{r['relation_id']} · {r['tool']} · {json.dumps(r['parameters'], ensure_ascii=False)}"
                for r in state["alternatives"]
            ]
        )
        if state["alternatives"]:
            alternative.current(0)

    def show_version(*_):
        if state["busy"] or not state["data"]:
            return
        snapshot = state["data"]["history"][version_choice.current()]
        state.update(snapshot=snapshot, roles={})
        graph = snapshot["graph"]
        files.delete(*files.get_children())
        relations.delete(*relations.get_children())
        for i, record in enumerate(graph["files"]):
            files.insert(
                "",
                "end",
                iid=str(i),
                values=(
                    record["path"],
                    next(k for k, v in ROLES.items() if v == record["role"]),
                    "人工指定" if record["role_basis"] == "user_supplied" else "依檔名推論",
                ),
            )
        for edge in graph["relations"]:
            relations.insert(
                "",
                "end",
                iid=edge["relation_id"],
                values=(
                    edge["from"],
                    edge["to"],
                    edge["tool"],
                    LABELS.get(edge["confidence_label"], edge["confidence_label"]),
                ),
            )
        status.set(
            f"來源 v{graph['source_version']} · 證據圖 v{snapshot['version']}。"
            + (
                "可決策；角色重比對會產生新推論，須重新確認。已採用的流程版本保持原狀，可到規則管理另行修訂。"
                if editable()
                else "歷史／封存／來源已變動，僅供查閱；請選最新版本或重新分析來源。"
            )
        )
        app._set_text(history_text, history_summary(graph))
        if relations.get_children():
            relations.selection_set(relations.get_children()[0])
        show_relation()
        buttons()

    def load(*_):
        if state["busy"] or graph_choice.current() < 0:
            return
        state["data"] = model.folder_graph(graphs[graph_choice.current()]["graph_id"])
        version_choice.configure(
            values=[f"v{g['version']} · {g['status']}" for g in state["data"]["history"]]
        )
        version_choice.current(len(state["data"]["history"]) - 1)
        show_version()

    def perform(work):
        if not editable():
            return
        state["busy"] = True
        buttons()

        def run():
            try:
                return work(), None
            except Exception as exc:
                return None, exc

        def done(result):
            state["busy"] = False
            if not window.winfo_exists():
                return
            if result[1]:
                buttons()
                app.error(result[1])
                return
            app.candidate_choice.set("")
            app.candidate_choice.configure(values=[])
            load()
            app.refresh_project()

        app._background(run, done)

    def decide(action):
        if state["roles"]:
            raise StudioError("INPUT_INVALID", "請先套用或取消尚未保存的角色修改。")
        edge = selected_edge()
        if not edge or not editable():
            return
        alt = (
            state["alternatives"][alternative.current()]["relation_id"]
            if alternative.current() >= 0
            else None
        )
        if action == "replace" and alt is None:
            return
        args = (
            state["snapshot"]["graph_id"],
            state["data"]["project_version"],
            edge["relation_id"],
            action,
            actor.get(),
            reason.get(),
        )
        perform(lambda: model.decide_folder_relation(*args, alternative_id=alt))

    def set_role():
        if not editable() or not files.selection() or role.get() not in ROLES:
            return
        identity = files.selection()[0]
        record = state["snapshot"]["graph"]["files"][int(identity)]
        state["roles"][record["path"]] = ROLES[role.get()]
        files.item(identity, values=(record["path"], role.get(), "待套用"))

    def apply_roles():
        if not state["roles"]:
            return
        args = (
            state["snapshot"]["graph_id"],
            state["data"]["project_version"],
            dict(state["roles"]),
            actor.get(),
            reason.get(),
        )
        perform(lambda: model.revise_folder_roles(*args))

    def preview(path):
        snapshot = state["snapshot"]
        asset = model.folder_asset(snapshot["graph_id"], snapshot["version"], path)
        child = tk.Toplevel(window)
        app.prepare_dialog(child)
        child.title(path)
        child.geometry("720x520")
        surface = SourcePreview(child, font=app.analysis.cget("font"))
        surface.pack(fill="both", expand=True)
        surface.show(asset["name"], asset["content"], asset["preview"])

    def preview_file():
        if files.selection():
            preview(state["snapshot"]["graph"]["files"][int(files.selection()[0])]["path"])

    def candidates():
        if not editable():
            return
        if state["roles"]:
            raise StudioError("INPUT_INVALID", "請先套用或取消尚未保存的角色修改。")
        result = model.candidates_from_graph(state["snapshot"]["graph_id"])
        app.show_folder_candidates((result, state["snapshot"]["graph"]["unknowns"]))
        window.destroy()

    def mutation(parent, label, callback):
        button = ttk.Button(parent, text=label, command=lambda: app.guard(callback))
        button.pack(side="left", padx=3)
        mutations.append(button)

    def close():
        if not state["roles"] or messagebox.askyesno(
            "未保存的角色", "捨棄尚未套用的角色修改並關閉？", parent=window
        ):
            window.destroy()

    def select_file(*_):
        if files.selection():
            record = state["snapshot"]["graph"]["files"][int(files.selection()[0])]
            selected = state["roles"].get(record["path"], record["role"])
            role.set(next(k for k, v in ROLES.items() if v == selected))

    mutation(file_controls, "設定選取檔案角色", set_role)
    mutation(file_controls, "套用角色並重新比對", apply_roles)
    mutation(file_controls, "取消角色修改", show_version)
    ttk.Button(file_controls, text="預覽此版本檔案", command=lambda: app.guard(preview_file)).pack(
        side="right"
    )
    for label, action in (
        ("確認關係", "confirm"),
        ("改為待確認", "unconfirm"),
        ("排除此關係", "remove"),
        ("替換為上方候選", "replace"),
    ):
        mutation(relation_controls, label, lambda a=action: decide(a))
    for label, side in (("預覽輸入", "from"), ("預覽成果", "to")):
        ttk.Button(
            relation_controls,
            text=label,
            command=lambda s=side: app.guard(
                lambda: preview(selected_edge()[s]) if selected_edge() else None
            ),
        ).pack(side="left", padx=3)
    mutation(controls, "依目前關係產生候選", candidates)
    ttk.Button(controls, text="關閉", command=close).pack(side="right")
    window.protocol("WM_DELETE_WINDOW", close)
    window.bind("<Escape>", lambda _: (close(), "break")[-1])

    def change_selection(callback):
        if state["roles"] and not messagebox.askyesno(
            "未保存的角色", "捨棄尚未套用的角色修改？", parent=window
        ):
            graph_choice.current(
                next(
                    i
                    for i, g in enumerate(graphs)
                    if g["graph_id"] == state["snapshot"]["graph_id"]
                )
            )
            version_choice.current(state["snapshot"]["version"] - 1)
            return
        callback()

    graph_choice.bind("<<ComboboxSelected>>", lambda _: app.guard(lambda: change_selection(load)))
    version_choice.bind(
        "<<ComboboxSelected>>", lambda _: app.guard(lambda: change_selection(show_version))
    )
    relations.bind("<<TreeviewSelect>>", show_relation)
    files.bind("<<TreeviewSelect>>", select_file)
    if graphs:
        preferred = [
            i for i, g in enumerate(graphs) if g["graph"]["source_id"] == app.selected_source
        ]
        graph_choice.current(preferred[-1] if preferred else len(graphs) - 1)
        load()
    buttons()
