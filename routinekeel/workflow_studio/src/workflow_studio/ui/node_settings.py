"""Typed controls for executable settings; no JSON or Python editing is required."""

import copy
import math
import tkinter as tk
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from tkinter import messagebox, ttk

from ..contracts import StudioError
from .widgets import BoundText

OPERATORS = {
    "存在": "exists",
    "非空": "nonempty",
    "文字完全相同": "equals",
    "SHA-256 相同": "sha256",
}
BRANCHES = {
    "檔案大於指定 bytes": "file_size_gt",
    "檔案至少指定 bytes": "file_size_gte",
    "文字包含": "text_contains",
    "文字完全相同": "text_equals",
}
TYPES = {"文字": "string", "整數": "integer", "數值": "number", "是／否": "boolean"}


def numeric(value, *, integer=False, money=False):
    try:
        if len(str(value)) > 256:
            raise ValueError()
        number = Decimal(str(value))
        if (
            not number.is_finite()
            or (not number.is_zero() and abs(number.adjusted()) > 1000)
            or (integer and number != number.to_integral_value())
        ):
            raise ValueError()
        if money:
            return int((number * 1_000_000).to_integral_value(rounding=ROUND_CEILING))
        if integer:
            return int(number)
        result = float(number)
        if not math.isfinite(result):
            raise ValueError()
        return result
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise StudioError("INPUT_INVALID", "請輸入有效的整數或有限小數。") from exc


class ConditionEditor(ttk.Frame):
    def __init__(self, parent, items, references, error):
        super().__init__(parent)
        self.items, self.error = copy.deepcopy(items), error
        self.selected, self.baseline = None, None
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(self, columns=("ref", "op", "value"), show="headings", height=5)
        for key, title in (("ref", "檢查對象"), ("op", "條件"), ("value", "預期值")):
            self.table.heading(key, text=title)
            self.table.column(key, width=150)
        self.table.grid(row=0, column=0, sticky="nsew")
        self.reference, self.operator, self.value = (
            tk.StringVar(),
            tk.StringVar(value="存在"),
            tk.StringVar(),
        )
        refs = list(dict.fromkeys(references + [item["reference"] for item in items]))
        for row, variable, values in (
            (1, self.reference, refs),
            (2, self.operator, list(OPERATORS)),
        ):
            ttk.Combobox(self, textvariable=variable, values=values, state="readonly").grid(
                row=row, column=0, sticky="ew", pady=3
            )
        if refs:
            self.reference.set(refs[0])
        expected = ttk.Entry(self, textvariable=self.value)
        expected.grid(row=3, column=0, sticky="ew", pady=3)

        def toggle_value(*_args):
            expected.configure(
                state="normal"
                if self.operator.get() in {"文字完全相同", "SHA-256 相同"}
                else "disabled"
            )

        self.operator.trace_add("write", toggle_value)
        toggle_value()
        ttk.Label(
            self, text="文字條件填預期內容；指紋條件填 64 位小寫 SHA-256。", wraplength=550
        ).grid(row=4, column=0, sticky="w", pady=4)
        buttons = ttk.Frame(self)
        buttons.grid(row=5, column=0, sticky="ew")
        ttk.Button(buttons, text="加入／更新檢查", command=self.apply).pack(side="left")
        ttk.Button(buttons, text="移除選取檢查", command=self.remove).pack(side="left", padx=6)
        ttk.Button(buttons, text="新增一筆", command=self.new).pack(side="left")
        self.table.bind("<<TreeviewSelect>>", self.select)
        self.refresh()
        self.baseline = self.snapshot()

    def snapshot(self):
        return self.reference.get(), self.operator.get(), self.value.get()

    def refresh(self):
        self.table.delete(*self.table.get_children())
        for i, item in enumerate(self.items):
            self.table.insert(
                "",
                "end",
                iid=str(i),
                values=(
                    item["reference"],
                    next(k for k, v in OPERATORS.items() if v == item["operator"]),
                    item.get("value") if item.get("value") is not None else "",
                ),
            )

    def select(self, _event=None):
        if not self.table.selection():
            return
        requested = int(self.table.selection()[0])
        if self.baseline is not None and self.snapshot() != self.baseline:
            try:
                self.apply(raise_error=True)
            except Exception as exc:
                self.error(exc)
                return
        self.selected = requested
        item = self.items[self.selected]
        self.reference.set(item["reference"])
        self.operator.set(next(k for k, v in OPERATORS.items() if v == item["operator"]))
        self.value.set(item.get("value") if item.get("value") is not None else "")
        self.baseline = self.snapshot()
        if self.table.selection() != (str(self.selected),):
            self.table.selection_set(str(self.selected))

    def new(self):
        try:
            if self.baseline is not None and self.snapshot() != self.baseline:
                self.apply(raise_error=True)
            self.reset()
        except Exception as exc:
            self.error(exc)

    def reset(self):
        self.selected = None
        if self.table.selection():
            self.table.selection_remove(*self.table.selection())
        self.value.set("")
        self.baseline = self.snapshot()

    def apply(self, *, raise_error=False):
        try:
            if not self.reference.get():
                raise StudioError("INPUT_INVALID", "請選擇檢查對象。")
            item = {
                "reference": self.reference.get(),
                "operator": OPERATORS[self.operator.get()],
                "value": self.value.get()
                if self.operator.get() in {"文字完全相同", "SHA-256 相同"}
                else None,
            }
            if self.selected is None:
                self.items.append(item)
            else:
                self.items[self.selected] = item
            self.refresh()
            self.reset()
        except Exception as exc:
            if raise_error:
                raise
            self.error(exc)

    def remove(self):
        if self.selected is not None:
            self.items.pop(self.selected)
            self.refresh()
            self.reset()

    def result(self):
        if self.snapshot() != self.baseline:
            self.apply(raise_error=True)
        return copy.deepcopy(self.items)


class ScrollableForm(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        canvas = tk.Canvas(self, highlightthickness=0, width=1, height=1)
        scroll = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        self.body = ttk.Frame(canvas, padding=12)
        item = canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(item, width=event.width))


def open_settings(app):
    model, project, revision = app.model, app.model.project(), app.model.revision()
    node = next(n for n in revision.ir["nodes"] if n["id"] == app.selected_node)
    tools = model.editor_tools()
    generated = (
        tools.get(f"{node['tool']}@{node['tool_version']}", {}).get("implementation")
        == "generated.program"
    )
    window = tk.Toplevel(app.root)
    app.prepare_dialog(window)
    window.title("步驟設定與完成條件")
    window.geometry(
        f"{min(820, app.root.winfo_screenwidth() - 80)}x{min(720, app.root.winfo_screenheight() - 120)}"
    )
    tabs = ttk.Notebook(window)
    tabs.pack(fill="both", expand=True, padx=12, pady=12)
    main, parameter_page = ttk.Frame(tabs, padding=12), ScrollableForm(tabs)
    parameters = parameter_page.body
    tabs.add(main, text="檔案與執行")
    tabs.add(parameter_page, text="處理規則")
    values = {}

    def field(parent, key, title, value, *, multiline=False, options=None, check=False):
        variable = tk.BooleanVar(value=value) if check else tk.StringVar(value=str(value))
        values[key] = variable
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=3)
        if check:
            widget = ttk.Checkbutton(row, text=title, variable=variable)
        else:
            ttk.Label(row, text=title, width=22).pack(side="left", anchor="n")
            widget = (
                BoundText(
                    row, variable, height=3, width=1, wrap="word", font=app.analysis.cget("font")
                )
                if multiline
                else ttk.Combobox(row, textvariable=variable, values=options, state="readonly")
                if options
                else ttk.Entry(row, textvariable=variable)
            )
        widget.pack(fill="x", expand=True)
        return widget

    field(main, "path", "輸出檔名／相對位置", node["outputs"]["result"]["path"])
    field(main, "timeout", "步驟時間上限（秒）", node["timeout_seconds"])
    cache = field(
        main, "cache", "重用相同輸入的快取", node["cache_policy"]["mode"] != "none", check=True
    )
    if node["side_effects"] == "external":
        cache.configure(state="disabled")
    ttk.Label(
        main, text="失敗時停止，不自動重送外部操作。修改後仍須重新試做與核准。", wraplength=550
    ).pack(anchor="w", pady=12)
    refs = [binding["source"] for binding in node["inputs"].values()]
    pre = ConditionEditor(tabs, node["preconditions"], refs, app.error)
    post = ConditionEditor(
        tabs,
        node["postconditions"],
        refs + [f"artifact://{node['id']}/{k}" for k in node["outputs"]],
        app.error,
    )
    tabs.add(pre, text="執行前檢查")
    tabs.add(post, text="完成條件")
    params = node["parameters"]
    branch_tools = {
        f"{tool['title']} · {key}": key
        for key, tool in tools.items()
        if tool["implementation"]
        in {"builtin.copy", "builtin.normalize", "builtin.json", "builtin.report"}
    }
    if node["kind"] == "composite_branch":
        field(
            parameters,
            "condition",
            "判斷方式",
            next(k for k, v in BRANCHES.items() if v == params["condition"]["operator"]),
            options=list(BRANCHES),
        )
        field(parameters, "threshold", "比較值", params["condition"]["value"])
        for side, label in (("when_true", "條件成立"), ("when_false", "條件不成立")):
            arm = params[side]
            key = f"{arm['tool']}@{arm['version']}"
            field(
                parameters,
                side,
                label + "時的動作",
                next((k for k, v in branch_tools.items() if v == key), key),
                options=list(branch_tools),
            )
            field(
                parameters,
                side + "_strip",
                label + "：去除前後空白",
                arm["parameters"].get("strip", False),
                check=True,
            )
            field(
                parameters,
                side + "_newline",
                label + "：結尾換行",
                arm["parameters"].get("final_newline", True),
                check=True,
            )
        ttk.Label(
            parameters, text="兩條路徑須有相同輸出契約；不允許巢狀分支或模型動作。", wraplength=550
        ).pack(anchor="w", pady=10)
    elif node["kind"] == "llm":
        policy = node["llm_policy"]
        for key, title, value in (
            ("endpoint", "模型 HTTPS 端點", params["endpoint"]),
            ("model", "模型名稱", policy["model"]),
            ("budget", "本步驟預算（USD）", policy["budget_micro_usd"] / 1_000_000),
            ("tokens", "最大輸出 Tokens", policy["max_output_tokens"]),
            ("calls", "最大呼叫數", policy["max_calls"]),
            ("llm_timeout", "模型時間上限（秒）", policy["timeout_seconds"]),
            ("input_rate", "每百萬輸入 Tokens USD", params["input_usd_per_million"]),
            ("output_rate", "每百萬輸出 Tokens USD", params["output_usd_per_million"]),
            ("pricing_note", "費率查核說明", params["pricing_note"]),
        ):
            field(parameters, key, title, value)
        field(parameters, "prompt", "處理要求", params["prompt"], multiline=True)
        output_page = ScrollableForm(tabs)
        fields_page = output_page.body
        tabs.add(output_page, text="模型輸出欄位")
        rows = []

        def add_field(name="", kind="string"):
            row = ttk.Frame(fields_page)
            row.pack(fill="x", pady=3)
            name_var, type_var = (
                tk.StringVar(value=name),
                tk.StringVar(value=next(k for k, v in TYPES.items() if v == kind)),
            )
            ttk.Entry(row, textvariable=name_var).pack(side="left", fill="x", expand=True)
            ttk.Combobox(
                row, textvariable=type_var, values=list(TYPES), state="readonly", width=12
            ).pack(side="left", padx=6)
            entry = (row, name_var, type_var)
            rows.append(entry)
            ttk.Button(row, text="移除", command=lambda: (rows.remove(entry), row.destroy())).pack(
                side="right"
            )

        for name, kind in params["output_fields"].items():
            add_field(name, kind)
        ttk.Button(fields_page, text="新增輸出欄位", command=add_field).pack(anchor="w", pady=8)
        ttk.Label(
            fields_page,
            text="金鑰沿用專案設定，不在流程表單輸入。變更模型或目的端後，試做需重新確認資料與憑證範圍。",
            wraplength=550,
        ).pack(anchor="w", pady=12)
    elif generated:
        ttk.Label(parameters, text=params["purpose"], wraplength=550).pack(anchor="w", pady=8)
        for name, kind in params.get("argument_types", {}).items():
            if kind != "null":
                field(
                    parameters,
                    "arg_" + name,
                    name,
                    params["arguments"][name],
                    check=kind == "boolean",
                )
    elif node["tool"] == "text.normalize":
        field(parameters, "strip", "去除每行前後空白", params.get("strip", False), check=True)
        field(parameters, "newline", "結尾保留換行", params.get("final_newline", True), check=True)
    else:
        ttk.Label(parameters, text="此動作沒有額外處理參數。", padding=12).pack(anchor="w")
    baseline = {k: v.get() for k, v in values.items()}
    original_conditions = copy.deepcopy((pre.items, post.items))

    def build():
        result = copy.deepcopy(params)
        llm = None
        if node["kind"] == "composite_branch":
            operator = BRANCHES[values["condition"].get()]
            result["condition"] = {
                "operator": operator,
                "value": numeric(values["threshold"].get(), integer=True)
                if operator.startswith("file_size")
                else values["threshold"].get(),
            }
            for side in ("when_true", "when_false"):
                key = branch_tools.get(values[side].get())
                if key is None:
                    raise StudioError("TOOL_NOT_ALLOWED", "請重新選擇可用分支動作。")
                tool = tools[key]
                result[side] = {
                    "tool": tool["tool_id"],
                    "version": tool["version"],
                    "parameters": {
                        "strip": values[side + "_strip"].get(),
                        "final_newline": values[side + "_newline"].get(),
                    }
                    if tool["implementation"] == "builtin.normalize"
                    else {},
                }
        elif node["kind"] == "llm":
            output_fields = {}
            for _, name, kind in rows:
                if not name.get().strip() or name.get().strip() in output_fields:
                    raise StudioError("INPUT_INVALID", "模型輸出欄位不可空白或重複。")
                output_fields[name.get().strip()] = TYPES[kind.get()]
            result.update(
                endpoint=values["endpoint"].get(),
                prompt=values["prompt"].get(),
                output_fields=output_fields,
                input_usd_per_million=numeric(values["input_rate"].get()),
                output_usd_per_million=numeric(values["output_rate"].get()),
                pricing_note=values["pricing_note"].get(),
            )
            llm = {
                **node["llm_policy"],
                "model": values["model"].get(),
                "budget_micro_usd": numeric(values["budget"].get(), money=True),
                "max_calls": numeric(values["calls"].get(), integer=True),
                "max_output_tokens": numeric(values["tokens"].get(), integer=True),
                "timeout_seconds": numeric(values["llm_timeout"].get(), integer=True),
            }
            if llm["timeout_seconds"] > numeric(values["timeout"].get(), integer=True):
                raise StudioError("INPUT_INVALID", "模型時間上限不能大於整個步驟的時間上限。")
        elif generated:
            for name, kind in params.get("argument_types", {}).items():
                if kind == "null":
                    continue
                value = values["arg_" + name].get()
                if value == baseline["arg_" + name]:
                    continue
                result["arguments"][name] = (
                    numeric(value, integer=kind == "integer")
                    if kind in {"integer", "number"}
                    else value
                )
        elif node["tool"] == "text.normalize":
            result = {"strip": values["strip"].get(), "final_newline": values["newline"].get()}
        return {
            "parameters": result,
            "llm_policy": llm,
            "output_path": values["path"].get(),
            "timeout_seconds": numeric(values["timeout"].get(), integer=True),
            "cacheable": values["cache"].get(),
            "preconditions": pre.result(),
            "postconditions": post.result(),
        }

    def save():
        try:
            model.update_node_settings(project.version, node["id"], **build())
            window.destroy()
            app.last_report = None
            app.reviewed.set(False)
            app.refresh_project()
        except Exception as exc:
            app.error(exc)

    def close():
        changed = (
            {k: v.get() for k, v in values.items()} != baseline
            or (pre.items, post.items) != original_conditions
            or pre.snapshot() != pre.baseline
            or post.snapshot() != post.baseline
        )
        if node["kind"] == "llm":
            changed |= [(name.get(), TYPES[kind.get()]) for _, name, kind in rows] != list(
                params["output_fields"].items()
            )
        if not changed or messagebox.askyesno("尚未儲存", "放棄這次步驟設定修改？", parent=window):
            window.destroy()

    footer = ttk.Frame(window, padding=12)
    footer.pack(fill="x")
    ttk.Button(footer, text="檢查並儲存新版本", command=save).pack(side="right")
    ttk.Button(footer, text="取消", command=close).pack(side="right", padx=8)
    window.protocol("WM_DELETE_WINDOW", close)
    window.bind("<Escape>", lambda _event: close())
