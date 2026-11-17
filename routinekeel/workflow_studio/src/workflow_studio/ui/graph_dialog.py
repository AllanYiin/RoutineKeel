"""A local graph draft: no model, filesystem writes or executable mutation until Save."""

import copy
import json
import tkinter as tk
from tkinter import messagebox, ttk

from ..contracts import StudioError
from ..ir.references import parse_reference


class GraphDraft:
    def __init__(self, ir):
        self.original = copy.deepcopy(ir)
        self.ir = copy.deepcopy(ir)

    def node(self, identity):
        found = next((n for n in self.ir["nodes"] if n["id"] == identity), None)
        if found is None:
            raise StudioError("INPUT_INVALID", "找不到選取的步驟。")
        return found

    def next_id(self):
        used = {n["id"] for n in self.ir["nodes"] + self.original["nodes"]}
        return next(f"step_{i}" for i in range(1, 1000) if f"step_{i}" not in used)

    def references(self, exclude=None):
        return [f"input://{k}" for k in self.ir["inputs"]] + [
            f"artifact://{node['id']}/{name}"
            for node in self.ir["nodes"]
            if node["id"] != exclude
            for name in node["outputs"]
        ]

    def reconnect(self, identity, source, dependencies, path):
        node = self.node(identity)
        old = node["inputs"]["source"]["source"]
        node["inputs"]["source"]["source"] = source
        for condition in node["preconditions"] + node["postconditions"]:
            if condition["reference"] == old:
                condition["reference"] = source
        ref = parse_reference(source)
        node["depends_on"] = sorted(
            set(dependencies) | ({ref.owner} if ref.scheme == "artifact" else set())
        )
        node["outputs"]["result"]["path"] = path

    def duplicate(self, identity):
        node = copy.deepcopy(self.node(identity))
        new_id = self.next_id()
        node["id"] = new_id
        for condition in node["preconditions"] + node["postconditions"]:
            prefix = f"artifact://{identity}/"
            if condition["reference"].startswith(prefix):
                condition["reference"] = (
                    f"artifact://{new_id}/" + condition["reference"][len(prefix) :]
                )
        self.ir["nodes"].append(node)
        return node

    def changes(self):
        return {
            "upsert_nodes": copy.deepcopy(self.ir["nodes"]),
            "delete_nodes": sorted(
                {n["id"] for n in self.original["nodes"]} - {n["id"] for n in self.ir["nodes"]}
            ),
            "inputs": copy.deepcopy(self.ir["inputs"]),
            "outputs": copy.deepcopy(self.ir["outputs"]),
        }


def open_graph(app):
    model = app.model
    project, revision = model.project(), model.revision()
    draft = GraphDraft(revision.ir)
    tools = model.editor_tools()
    simple = {
        k: t
        for k, t in tools.items()
        if t["implementation"]
        in {"builtin.copy", "builtin.normalize", "builtin.json", "builtin.report"}
    }
    choices = {f"{t['title']} · {k}": k for k, t in simple.items()}
    window = tk.Toplevel(app.root)
    app.prepare_dialog(window)
    window.title("步驟連線與最終成果")
    window.geometry(
        f"{min(950, app.root.winfo_screenwidth() - 80)}x{min(730, app.root.winfo_screenheight() - 120)}"
    )
    window.columnconfigure(1, weight=1)
    window.rowconfigure(1, weight=1)
    ttk.Label(
        window,
        text="在這裡新增或重用動作、調整來源與交付物，全部檢查通過後才另存新版本。",
        padding=12,
    ).grid(row=0, column=0, columnspan=2, sticky="w")
    left = ttk.Frame(window, padding=(12, 0, 6, 0))
    left.grid(row=1, column=0, sticky="nsew")
    listing = tk.Listbox(left, width=30, exportselection=False)
    listing.pack(fill="both", expand=True)
    action = ttk.Combobox(left, values=list(choices), state="readonly", width=28)
    action.pack(fill="x", pady=6)
    if choices:
        action.current(0)
    right = ttk.Frame(window, padding=(6, 0, 12, 0))
    right.grid(row=1, column=1, sticky="nsew")
    right.columnconfigure(1, weight=1)
    title = tk.StringVar()
    source, path = tk.StringVar(), tk.StringVar()
    ttk.Label(right, textvariable=title).grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
    )
    ttk.Label(right, text="輸入來源").grid(row=1, column=0, sticky="w")
    source_choice = ttk.Combobox(right, textvariable=source, state="readonly")
    source_choice.grid(row=1, column=1, sticky="ew", pady=4)
    ttk.Label(right, text="輸出檔名／相對位置").grid(row=2, column=0, sticky="w")
    ttk.Entry(right, textvariable=path).grid(row=2, column=1, sticky="ew", pady=4)
    ttk.Label(right, text="其他前置步驟（可複選）").grid(row=3, column=0, sticky="nw", pady=6)
    deps = tk.Listbox(right, height=4, selectmode="multiple", exportselection=False)
    deps.grid(row=3, column=1, sticky="ew", pady=6)
    ttk.Label(right, text="輸入來源所屬步驟會自動列入依賴。", foreground="#64748B").grid(
        row=4, column=0, columnspan=2, sticky="w"
    )
    strip, newline = tk.BooleanVar(), tk.BooleanVar()
    options = ttk.Frame(right)
    options.grid(row=5, column=0, columnspan=2, sticky="ew", pady=8)
    strip_widget = ttk.Checkbutton(options, text="去除每行前後空白", variable=strip)
    strip_widget.pack(side="left")
    newline_widget = ttk.Checkbutton(options, text="結尾保留換行", variable=newline)
    newline_widget.pack(side="left")
    ttk.Separator(right).grid(row=6, column=0, columnspan=2, sticky="ew", pady=8)
    input_key = next(iter(draft.ir["inputs"]))
    input_format = tk.StringVar(value=draft.ir["inputs"][input_key].get("format") or "未指定")
    ttk.Label(right, text="原始輸入檔格式").grid(row=7, column=0, sticky="w")
    ttk.Combobox(
        right, textvariable=input_format, values=["未指定", "txt", "csv", "json"], state="readonly"
    ).grid(row=7, column=1, sticky="ew")
    ttk.Label(
        right, text="此設定宣告輸入格式，不會自動轉檔；實際內容仍需試做。", foreground="#64748B"
    ).grid(row=8, column=0, columnspan=2, sticky="w", pady=5)
    ttk.Label(right, text="最終交付物").grid(row=9, column=0, columnspan=2, sticky="w", pady=(8, 4))
    deliverables = ttk.Treeview(right, columns=("name", "source"), show="headings", height=4)
    deliverables.heading("name", text="交付名稱")
    deliverables.heading("source", text="使用哪個步驟的成果")
    deliverables.column("name", width=100)
    deliverables.column("source", width=200)
    deliverables.grid(row=10, column=0, columnspan=2, sticky="nsew")
    right.rowconfigure(10, weight=1)
    output_name, output_ref = tk.StringVar(), tk.StringVar()
    ttk.Entry(right, textvariable=output_name).grid(row=11, column=0, sticky="ew", pady=6)
    output_choice = ttk.Combobox(right, textvariable=output_ref, state="readonly")
    output_choice.grid(row=11, column=1, sticky="ew", pady=6)
    state = {"selected": None, "deps": [], "loading": False, "references": {}}

    def reference_label(value):
        ref = parse_reference(value)
        if ref.scheme == "input":
            label = "原始輸入 · " + ref.owner
        else:
            node = next((n for n in draft.ir["nodes"] if n["id"] == ref.owner), None)
            label = (
                f"{ref.owner} · {app.tool_titles.get(node['tool'], node['tool'])} → {ref.port}"
                if node
                else f"已移除的步驟 · {ref.owner} → {ref.port}"
            )
        state["references"][label] = value
        return label

    def capture():
        if state["selected"] is not None:
            draft.reconnect(
                state["selected"],
                state["references"].get(source.get(), source.get()),
                [state["deps"][i] for i in deps.curselection()],
                path.get(),
            )
            node = draft.node(state["selected"])
            if node["tool"] == "text.normalize":
                node["parameters"] = {"strip": strip.get(), "final_newline": newline.get()}
        draft.ir["inputs"][input_key]["format"] = (
            None if input_format.get() == "未指定" else input_format.get()
        )

    def refresh(selected=None):
        state["loading"] = True
        listing.delete(0, "end")
        for node in draft.ir["nodes"]:
            listing.insert(
                "end", f"{node['id']} · {app.tool_titles.get(node['tool'], node['tool'])}"
            )
        deliverables.delete(*deliverables.get_children())
        for name, ref in draft.ir["outputs"].items():
            deliverables.insert("", "end", iid=name, values=(name, reference_label(ref)))
        output_choice.configure(
            values=[
                reference_label(ref) for ref in draft.references() if ref.startswith("artifact://")
            ]
        )
        state["selected"] = selected
        if selected is not None:
            node = draft.node(selected)
            listing.selection_set(
                next(i for i, n in enumerate(draft.ir["nodes"]) if n["id"] == selected)
            )
            title.set(f"{selected} · {app.tool_titles.get(node['tool'], node['tool'])}")
            source_choice.configure(
                values=[reference_label(ref) for ref in draft.references(exclude=selected)]
            )
            source.set(reference_label(node["inputs"]["source"]["source"]))
            path.set(node["outputs"]["result"]["path"])
            state["deps"] = [n["id"] for n in draft.ir["nodes"] if n["id"] != selected]
            deps.delete(0, "end")
            for i, identity in enumerate(state["deps"]):
                deps.insert("end", identity)
                if identity in node["depends_on"]:
                    deps.selection_set(i)
            strip.set(node["parameters"].get("strip", False))
            newline.set(node["parameters"].get("final_newline", True))
            for widget in (strip_widget, newline_widget):
                widget.configure(state="normal" if node["tool"] == "text.normalize" else "disabled")
        else:
            title.set("請先新增或選取動作")
            source.set("")
            path.set("")
            deps.delete(0, "end")
        state["loading"] = False

    def select(_event=None):
        if state["loading"] or not listing.curselection():
            return
        selected = draft.ir["nodes"][listing.curselection()[0]]["id"]
        if selected == state["selected"]:
            return
        capture()
        refresh(selected)

    def add(clone=False):
        capture()
        if clone:
            if state["selected"] is None:
                return
            node = draft.duplicate(state["selected"])
        else:
            if action.get() not in choices:
                return
            key = choices[action.get()]
            identity = draft.next_id()
            extension = simple[key]["outputs"]["result"].get("format") or "bin"
            node = model.node_template(
                identity, key, f"input://{input_key}", f"artifacts/{identity}.{extension}"
            )
            draft.ir["nodes"].append(node)
        refresh(node["id"])

    def remove():
        identity = state["selected"]
        if identity is None:
            return
        if not messagebox.askyesno(
            "從草稿移除步驟", "移除後請調整引用與交付物；儲存時會一起檢查。", parent=window
        ):
            return
        capture()
        draft.ir["nodes"] = [n for n in draft.ir["nodes"] if n["id"] != identity]
        refresh(draft.ir["nodes"][0]["id"] if draft.ir["nodes"] else None)

    for label, command in (
        ("新增選取動作", lambda: add()),
        ("重用選取步驟", lambda: add(True)),
        ("移除選取步驟", remove),
    ):
        ttk.Button(left, text=label, command=lambda command=command: app.guard(command)).pack(
            fill="x", pady=3
        )

    def output_action(remove=False):
        name = output_name.get().strip()
        if not name:
            raise StudioError("INPUT_INVALID", "請填寫交付名稱。")
        if remove:
            draft.ir["outputs"].pop(name, None)
            output_name.set("")
            output_ref.set("")
        else:
            if not output_ref.get():
                raise StudioError("INPUT_INVALID", "請選取交付物來源。")
            draft.ir["outputs"][name] = state["references"].get(output_ref.get(), output_ref.get())
        capture()
        refresh(state["selected"])

    def select_output(_event=None):
        if deliverables.selection():
            name = deliverables.selection()[0]
            output_name.set(name)
            output_ref.set(reference_label(draft.ir["outputs"][name]))

    buttons = ttk.Frame(right)
    buttons.grid(row=12, column=0, columnspan=2, sticky="ew")
    ttk.Button(buttons, text="加入／更新交付物", command=lambda: app.guard(output_action)).pack(
        side="left"
    )
    ttk.Button(
        buttons, text="移除交付物", command=lambda: app.guard(lambda: output_action(True))
    ).pack(side="left", padx=6)

    def close():
        try:
            capture()
            changed = json.dumps(draft.ir, sort_keys=True) != json.dumps(
                draft.original, sort_keys=True
            )
            changed = changed or bool(
                (output_name.get() or output_ref.get())
                and draft.ir["outputs"].get(output_name.get())
                != state["references"].get(output_ref.get(), output_ref.get())
            )
        except Exception:
            changed = True
        if not changed or messagebox.askyesno("尚未儲存", "放棄這次流程編輯？", parent=window):
            window.destroy()

    def save():
        try:
            capture()
            if output_name.get() or output_ref.get():
                output_action()
            model.edit_graph(project.version, **draft.changes())
            window.destroy()
            app.last_report = None
            app.reviewed.set(False)
            app.refresh_project()
        except Exception as exc:
            app.error(exc)

    footer = ttk.Frame(window, padding=12)
    footer.grid(row=2, column=0, columnspan=2, sticky="ew")
    ttk.Button(footer, text="檢查並儲存新版本", command=save).pack(side="right")
    ttk.Button(footer, text="取消", command=close).pack(side="right", padx=8)
    window.bind("<Escape>", lambda _event: close())
    window.protocol("WM_DELETE_WINDOW", close)
    listing.bind("<<ListboxSelect>>", lambda event: app.guard(lambda: select(event)))
    deliverables.bind("<<TreeviewSelect>>", select_output)
    refresh(draft.ir["nodes"][0]["id"])
    listing.focus_set()
