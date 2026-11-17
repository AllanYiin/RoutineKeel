"""Native offline-first workbench; model work streams on a separate service connection."""

import io
import json
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

from ..contracts import StudioError
from ..ingestion.preview import fit_size
from .display import configure_display, enable_high_dpi
from .model import StudioUIModel, analysis_text

COLORS = {
    "bg": "#F8FAFC",
    "panel": "#FFFFFF",
    "text": "#0F172A",
    "accent": "#2563EB",
    "muted": "#475569",
}


class StudioApp:
    def __init__(self, workspace, *, root=None, model=None):
        self.dpi_mode = enable_high_dpi() if root is None else "provided-root"
        self.root = root or tk.Tk()
        self.root.title("RoutineKeel｜工作流程製作工作台")
        self.display = configure_display(self.root)
        self.root.configure(bg=COLORS["bg"])
        self.model = model or StudioUIModel(workspace)
        self.tool_titles = self.model.tool_titles()
        self.messages = queue.Queue()
        self.cancelled = threading.Event()
        self.busy = self.close_requested = False
        self.project_rows, self.source_rows = [], {}
        self.current_project = self.selected_source = self.selected_node = None
        self.display_image = None
        self.raw_analysis = ""
        self.buttons = []
        self.button_rules = {}
        self.display_ir = None
        self.case_count = 0
        self.case_history_count = 0
        self.last_report = None
        self._build()
        self.refresh_projects()
        self.root.update_idletasks()
        width = self.panes.winfo_width()
        self.panes.sashpos(0, round(width * 0.25))
        self.panes.sashpos(1, round(width * 0.66))
        self.draw_workflow(refresh=False)
        self.refresh_actions()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._drain)

    def button(self, parent, text, command, *, requires=None, style="TButton", **pack):
        widget = ttk.Button(
            parent, text=text, command=lambda: self.guard(command), style=style, width=0
        )
        widget.pack(**pack)
        self.buttons.append(widget)
        self.button_rules[widget] = requires
        return widget

    def refresh_actions(self):
        project = self.current_project
        editable = project is not None and project.status == "active"
        dirty = self.has_unsaved_changes()
        active = editable and not dirty
        source = active and self.selected_source in self.source_rows
        workflow = active and self.display_ir is not None
        verified = bool(
            self.last_report
            and self.last_report.get("status") == "passed"
            and self.last_report.get("build_id") == self.model.build_id
        )
        enabled = {
            None: True,
            "project": project is not None,
            "active": active,
            "editable": editable,
            "new": project is None and bool(self.name_var.get().strip()),
            "archived": project is not None and project.status == "archived",
            "source": source,
            "analysis": source and self.model.provider is not None,
            "candidate": active
            and bool(self.model.candidates)
            and self.candidate_choice.current() >= 0,
            "node": workflow and self.selected_node is not None,
            "workflow": workflow,
            "cases": active and self.case_count > 0,
            "case_history": active and self.case_history_count > 0,
            "verify": workflow and self.case_count > 0,
            "repair": active
            and self.model.provider is not None
            and bool(
                self.last_report
                and self.last_report.get("status") == "failed"
                and self.last_report.get("static") == "passed"
                and self.last_report.get("error_code") == "TEST_FAILED"
                and self.last_report.get("build_id") == self.model.build_id
            ),
            "publish": active
            and verified
            and self.reviewed.get()
            and bool(self.actor_var.get().strip()),
        }
        for button, rule in self.button_rules.items():
            button.configure(
                state="normal" if not self.busy and enabled.get(rule, False) else "disabled"
            )
        self.candidate_choice.configure(
            state="readonly" if not self.busy and active and self.model.candidates else "disabled"
        )
        if hasattr(self, "save_state"):
            self.save_state.set("尚未儲存" if dirty else ("已儲存" if project else ""))

    def has_unsaved_changes(self):
        if not hasattr(self, "name_var"):
            return False
        values = (
            self.name_var.get().strip(),
            self.goal_var.get(),
            [s.strip() for s in self.success_var.get().splitlines() if s.strip()],
        )
        p = self.current_project
        if p is None:
            return any(values)
        return values != (p.name, p.goal.get("description", ""), p.goal.get("success_criteria", []))

    def confirm_leave(self):
        if not self.has_unsaved_changes():
            return True
        answer = messagebox.askyesnocancel(
            "尚未儲存的工作目標", "要先儲存目前輸入的內容嗎？", parent=self.root
        )
        if answer is None:
            return False
        if answer:
            try:
                self.save_project() if self.current_project else self.create_project()
            except Exception as exc:
                self.error(exc)
                return False
        return True

    def clear_project_view(self):
        self.model.project_id = self.model.build_id = self.model.approval_id = None
        self.model.candidates = []
        self.current_project = self.selected_source = self.selected_node = None
        self.display_ir = self.last_report = self.display_image = None
        self.case_count = 0
        self.case_history_count = 0
        self.name_var.set("")
        self.goal_var.set("")
        self.success_var.set("")
        self.source_rows = {}
        self.source_tree.delete(*self.source_tree.get_children())
        self.candidate_choice["values"] = []
        self.candidate_choice.set("")
        self.image_label.configure(image="")
        self.preview_label.configure(text="尚未選取來源")
        self._set_text(self.preview_text, "從左側選取來源，即可查看實際內容。")
        self._set_text(self.analysis, "分析過程與待確認問題會顯示在這裡。")
        self._set_text(self.report_text, "完成流程與成功案例設定後，即可開始試做。")
        self.case_label.set("尚未設定試做案例")
        self.reviewed.set(False)
        self.draw_workflow(refresh=False)
        self.refresh_actions()

    def start_new_project(self):
        if self.confirm_leave():
            self.clear_project_view()
            self.project_list.selection_clear(0, "end")
            self.name_entry.focus_set()
            self.status.set("輸入專案名稱與工作目標，再按「建立專案」。")

    def _build(self):
        from .layout import build_workbench

        build_workbench(self)

    def guard(self, action):
        if self.busy:
            return
        try:
            action()
        except Exception as exc:
            self.error(exc)

    def error(self, exc):
        text = (
            str(exc)
            if isinstance(exc, StudioError)
            else f"操作未完成（{type(exc).__name__}），請檢查輸入與本機環境。"
        )
        self.status.set(text)
        messagebox.showerror("目前無法完成", text, parent=self.root)

    def _set_text(self, widget, text):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def refresh_projects(self):
        self.project_rows = self.model.projects(self.archived.get())
        self.project_list.delete(0, "end")
        for p in self.project_rows:
            self.project_list.insert(
                "end", p.name + ("（已封存）" if p.status == "archived" else "")
            )

    def _select_project(self, _event=None):
        if self.busy or not self.project_list.curselection():
            return
        self.guard(
            lambda: self.load_project(
                self.project_rows[self.project_list.curselection()[0]].project_id
            )
        )

    def load_project(self, identity):
        if self.current_project and identity != self.model.project_id and not self.confirm_leave():
            return
        if identity != self.model.project_id or self.current_project is None:
            self.model.select_project(identity)
        self.refresh_project()

    def refresh_project(self):
        self.current_project = self.model.project()
        p = self.current_project
        self.name_var.set(p.name)
        self.goal_var.set(p.goal.get("description", ""))
        self.success_var.set("\n".join(p.goal.get("success_criteria", [])))
        self.source_tree.delete(*self.source_tree.get_children())
        self.source_rows = {}
        for source in self.model.sources():
            self.source_tree.insert(
                "",
                "end",
                iid=source.source_id,
                text=f"{source.name} · 第 {source.version} 版",
                open=True,
            )
            self.source_rows[source.source_id] = (source, None)
            for i, record in enumerate(source.manifest["files"]):
                identity = source.source_id + ":" + str(i)
                self.source_tree.insert(source.source_id, "end", iid=identity, text=record["path"])
                self.source_rows[identity] = (source, record)
        self.case_count = len(self.model.cases())
        self.case_history_count = len(self.model.cases(include_archived=True))
        self.case_label.set(f"已設定 {self.case_count} 個固定成功案例")
        if self.selected_source in self.source_rows:
            self.source_tree.selection_set(self.selected_source)
        else:
            self.selected_source = None
        self.draw_workflow()
        self.reviewed.set(False)
        self.refresh_actions()

    def create_project(self):
        p = self.model.create_project(
            self.name_var.get(), self.goal_var.get(), self.success_var.get()
        )
        self.refresh_projects()
        self.refresh_project()
        self.status.set(f"已建立「{p.name}」。接著提供工作說明或成功案例。")

    def save_project(self):
        self.model.update_project(
            self.name_var.get(),
            self.goal_var.get(),
            self.success_var.get(),
            self.current_project.version,
        )
        self.refresh_projects()
        self.refresh_project()
        self.status.set("目標已保存；修改後需重新核對流程與試做結果。")

    def _archive(self, restore):
        self.model.archive(restore)
        self.refresh_projects()
        self.refresh_project()

    def choose_workspace(self):
        if not self.confirm_leave():
            return
        path = filedialog.askdirectory(title="選擇 Studio 工作區", parent=self.root)
        if path:
            self.model = StudioUIModel(path)
            self.tool_titles = self.model.tool_titles()
            self.clear_project_view()
            self.refresh_projects()
            self.workspace_label.configure(text=f"工作區：{self.model.workspace}")
            self.status.set("已切換工作區，請選擇或建立專案。")

    def import_file(self):
        path = filedialog.askopenfilename(title="選擇來源檔案", parent=self.root)
        if path:
            self.model.import_path(path)
            self.refresh_project()

    def import_folder(self):
        path = filedialog.askdirectory(title="選擇已成功完成工作的資料夾", parent=self.root)
        if path:
            source = self.model.import_path(path)
            self.refresh_project()
            self.source_tree.selection_set(source.source_id)
            self._source_selected()

    def paste_source(self):
        window = tk.Toplevel(self.root)
        self.prepare_dialog(window)
        window.title("貼上工作說明或 Mermaid")
        text = tk.Text(window, width=70, height=18)
        text.pack(padx=12, pady=12)
        mermaid = tk.BooleanVar(value=False)
        ttk.Checkbutton(window, text="這是 Mermaid 流程圖", variable=mermaid).pack(
            anchor="w", padx=12
        )

        def save():
            try:
                self.model.import_text(
                    text.get("1.0", "end-1c"), "流程.mmd" if mermaid.get() else "工作說明.txt"
                )
                window.destroy()
                self.refresh_project()
            except Exception as exc:
                self.error(exc)

        ttk.Button(window, text="預覽並匯入", command=save).pack(pady=10)

    def _source_selected(self, _event=None):
        if self.busy or not self.source_tree.selection():
            return
        source, record = self.source_rows[self.source_tree.selection()[0]]
        self.selected_source = source.source_id
        self.display_image = None
        self.image_label.configure(image="")
        self.preview_label.configure(text=f"{source.name} · 第 {source.version} 版")
        if record is None:
            self._set_text(
                self.preview_text, "\n".join(r["path"] for r in source.manifest["files"])
            )
        elif record["preview"]["kind"] == "image":
            with Image.open(
                io.BytesIO(self.model.content(source.source_id, record["path"]))
            ) as image:
                size = fit_size(
                    image.width, image.height, max(260, self.image_label.winfo_width()), 300
                )
                self.display_image = ImageTk.PhotoImage(image.resize(size))
            self.image_label.configure(image=self.display_image)
            self._set_text(
                self.preview_text,
                f"{record['preview']['width']} × {record['preview']['height']} 像素",
            )
        else:
            self._set_text(
                self.preview_text,
                record["preview"]["text"]
                + (
                    "\n（預覽已截斷，原始副本仍完整保存。）"
                    if record["preview"]["truncated"]
                    else ""
                ),
            )
        self.tabs.select(0)
        self.refresh_actions()

    def replace_source(self):
        if not self.selected_source:
            raise StudioError("SOURCE_NOT_FOUND", "請先選擇來源。")
        path = filedialog.askopenfilename(title="選擇替換來源", parent=self.root)
        if path:
            self.model.import_path(path, self.selected_source)
            self.refresh_project()

    def remove_source(self):
        if self.selected_source and messagebox.askyesno(
            "移除參照", "只移除 Studio 參照，保留原始檔與歷史。", parent=self.root
        ):
            self.model.remove_source(self.selected_source)
            self.refresh_project()

    def _background(self, action, done):
        self.busy = True
        self.cancelled.clear()
        for button in self.buttons:
            button.configure(state="disabled")

        def work():
            try:
                self.messages.put(("done", done, action()))
            except Exception as exc:
                self.messages.put(("error", None, exc))

        threading.Thread(target=work, daemon=False).start()

    def _drain(self):
        try:
            while True:
                kind, callback, value = self.messages.get_nowait()
                if kind == "event":
                    if value["event"] in {"analysis.started", "repair.started"}:
                        self.raw_analysis = ""
                        self._set_text(self.analysis, "正在整理候選流程……")
                        if value["event"] == "repair.started":
                            self.status.set(
                                f"第 {value['repair_round']} 輪修復；固定驗收條件保持不變。"
                            )
                    elif value["event"] == "analysis.repairing":
                        self.status.set(
                            f"候選流程未通過檢查，正在進行第 {value['repair_round']} 次修正。"
                        )
                    elif value["event"] == "repair.verified":
                        self.status.set(f"第 {value['repair_round']} 輪重測：{value['status']}。")
                    elif value["event"] == "repair.round_failed":
                        self.status.set("修復提案未通過契約檢查，仍受兩輪總上限限制。")
                    elif value["event"] == "repair.needs_clarification":
                        self._set_text(
                            self.analysis,
                            analysis_text(self.raw_analysis)
                            + "\n\n待確認：\n"
                            + "\n".join(value["questions"]),
                        )
                    elif value["event"] in {"analysis.delta", "repair.delta"}:
                        self.raw_analysis += value["content"]
                        self._set_text(self.analysis, analysis_text(self.raw_analysis))
                    continue
                self.busy = False
                self.stop_button.configure(state="disabled")
                if self.close_requested:
                    self.root.destroy()
                    return
                if kind == "error":
                    self.error(value)
                else:
                    self.guard(lambda: callback(value))
                self.refresh_actions()
                if self.close_requested:
                    self.root.destroy()
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._drain)

    def find_folder_candidates(self):
        if not self.selected_source:
            raise StudioError("SOURCE_NOT_FOUND", "請先選擇成功案例資料夾。")
        self.status.set("正在比對本機樣本，尋找有證據的處理方式……")

        def done(result):
            candidates, unknowns = result
            self.candidate_choice["values"] = [
                f"候選 {i + 1} · "
                + " → ".join(self.tool_titles.get(n["tool"], "自訂處理") for n in c["ir"]["nodes"])
                for i, c in enumerate(candidates)
            ]
            if candidates:
                self.candidate_choice.current(0)
            self.status.set(
                f"找到 {len(candidates)} 個候選；{len(unknowns)} 項仍缺少證據。請查看後再採用。"
            )
            self.refresh_project()

        self._background(lambda: self.model.folder_candidates(self.selected_source), done)

    def accept_candidate(self):
        index = self.candidate_choice.current()
        if index < 0:
            raise StudioError("EVIDENCE_INSUFFICIENT", "目前沒有選取候選流程。")
        self.model.accept_candidate(index)
        self.refresh_project()
        self.status.set("已保存候選流程及固定成功案例。接著進行試做。")

    def draw_workflow(self, *, refresh=True):
        if refresh:
            revision = self.model.revision() if self.model.project_id else None
            self.display_ir = (
                revision.ir
                if revision
                and revision.status in {"ir_ready", "verified", "approved", "published"}
                and "nodes" in revision.ir
                else None
            )
        self.canvas.delete("all")
        scale = self.display["scale"]
        width = max(round(280 * scale), self.canvas.winfo_width() - 4)
        if not self.display_ir:
            self.canvas.create_text(
                width / 2,
                round(70 * scale),
                text="先建立專案，再提供工作說明或成功案例。\n整理出的工作步驟會顯示在這裡。",
                fill=COLORS["muted"],
                width=width - round(40 * scale),
                font=(self.display["font_family"], 11),
                justify="center",
            )
            self.canvas.configure(scrollregion=(0, 0, width, round(160 * scale)))
            self.selected_node = None
            return
        nodes = self.display_ir["nodes"]
        levels = {}
        pending = {node["id"]: node for node in nodes}
        while pending:
            ready = [node for node in pending.values() if set(node["depends_on"]) <= levels.keys()]
            if not ready:
                return
            for node in ready:
                levels[node["id"]] = max((levels[key] + 1 for key in node["depends_on"]), default=0)
                del pending[node["id"]]
        grouped = {}
        for node in nodes:
            grouped.setdefault(levels[node["id"]], []).append(node)
        width = max(
            width, max((len(group) for group in grouped.values()), default=1) * round(180 * scale)
        )
        positions = {}
        for level, group in grouped.items():
            for index, node in enumerate(group):
                positions[node["id"]] = (
                    width * (index + 0.5) / len(group),
                    round((45 + level * 90) * scale),
                    min(round(290 * scale), width / len(group) - round(24 * scale)),
                )
        for node in nodes:
            x, y, box_width = positions[node["id"]]
            for parent in node["depends_on"]:
                px, py, _ = positions[parent]
                self.canvas.create_line(
                    px,
                    py + 23 * scale,
                    x,
                    y - 23 * scale,
                    arrow="last",
                    fill="#94A3B8",
                    width=max(1, round(1.5 * scale)),
                )
        for node in nodes:
            x, y, box_width = positions[node["id"]]
            selected = node["id"] == self.selected_node
            tag = node["id"]
            self.canvas.create_rectangle(
                x - box_width / 2,
                y - 23 * scale,
                x + box_width / 2,
                y + 23 * scale,
                fill="#DBEAFE" if selected else "#F0F7FF",
                outline="#2563EB" if selected else "#BFDBFE",
                width=max(1, round(scale)),
                tags=(tag,),
            )
            self.canvas.create_text(
                x,
                y,
                text=self.tool_titles.get(node["tool"], "自訂處理"),
                fill=COLORS["text"],
                font=(self.display["font_family"], 11),
                width=box_width - 16 * scale,
                tags=(tag,),
            )
            self.canvas.tag_bind(tag, "<Button-1>", lambda _event, n=node: self.select_node(n))
        if self.selected_node not in positions:
            self.selected_node = None
        self.canvas.configure(
            scrollregion=(0, 0, width, round((90 + max(levels.values(), default=0) * 90) * scale))
        )

    def select_node(self, node):
        if self.busy:
            return
        self.selected_node = node["id"]
        self.node_info.set(
            f"{self.tool_titles.get(node['tool'], '自訂處理')}｜{len(node['inputs'])} 項輸入 → {len(node['outputs'])} 項輸出\n每次執行都會檢查完成條件，修改後須重新試做。"
        )
        if node["tool"].startswith("data.transform_"):
            self.node_info.set(
                node["parameters"]["purpose"]
                + "\n由模型產生的資料處理，須通過獨立試做與核准後才可發布。"
            )
        self.draw_workflow(refresh=False)
        self.refresh_actions()

    def edit_node(self):
        if not self.selected_node:
            raise StudioError("INPUT_INVALID", "請點選要調整的步驟。")
        expected_version = self.model.project().version
        node = next(n for n in self.model.revision().ir["nodes"] if n["id"] == self.selected_node)
        name = simpledialog.askstring(
            "調整步驟",
            "此步驟輸出檔案的名稱",
            initialvalue=Path(node["outputs"]["result"]["path"]).name,
            parent=self.root,
        )
        if name:
            strip = (
                messagebox.askyesno("整理空白", "是否移除每行前後空白？", parent=self.root)
                if node["tool"] == "text.normalize"
                else None
            )
            arguments = None
            if node["tool"].startswith("data.transform_"):
                arguments = dict(node["parameters"].get("arguments", {}))
                for key, kind in node["parameters"].get("argument_types", {}).items():
                    if kind == "null":
                        continue
                    prompt = f"{node['parameters']['purpose']}\n\n{key}"
                    if kind == "boolean":
                        value = messagebox.askyesnocancel(
                            "調整資料規則", prompt + "：是否啟用？", parent=self.root
                        )
                    elif kind in {"integer", "number"}:
                        ask = (
                            simpledialog.askinteger if kind == "integer" else simpledialog.askfloat
                        )
                        value = ask(
                            "調整資料規則", prompt, initialvalue=arguments[key], parent=self.root
                        )
                    else:
                        value = simpledialog.askstring(
                            "調整資料規則", prompt, initialvalue=arguments[key], parent=self.root
                        )
                    if value is None:
                        return
                    arguments[key] = value
            self.model.edit_node(
                self.selected_node,
                output_name=name,
                strip=strip,
                arguments=arguments,
                expected_version=expected_version,
            )
            self.refresh_project()

    def delete_node(self):
        if self.selected_node and messagebox.askyesno(
            "刪除步驟", "只有未被其他步驟或成果使用的節點可以刪除。", parent=self.root
        ):
            self.model.edit_node(self.selected_node, delete=True)
            self.refresh_project()

    def edit_graph(self):
        from .graph_dialog import open_graph

        open_graph(self)

    def edit_evidence(self):
        from .evidence_dialog import open_evidence

        open_evidence(self)

    def add_case(self):
        source = filedialog.askopenfilename(title="選擇試做輸入", parent=self.root)
        expected = (
            filedialog.askopenfilename(title="選擇正確成果樣本", parent=self.root)
            if source
            else None
        )
        if expected:
            outputs = list(self.model.revision().ir["outputs"]) if self.model.revision() else []
            selected = 1
            if len(outputs) > 1:
                selected = simpledialog.askinteger(
                    "選擇交付物",
                    "這份正確成果要比對哪個交付物？\n"
                    + "\n".join(f"{i + 1}. {name}" for i, name in enumerate(outputs)),
                    minvalue=1,
                    maxvalue=len(outputs),
                    parent=self.root,
                )
                if selected is None:
                    return
            rule = simpledialog.askinteger(
                "成果比較規則",
                "1. 完全相同：包含空白、換行與欄位順序。\n"
                "2. JSON 內容相同：忽略排版、物件欄位順序與等值數字寫法；\n"
                "   值、型別及陣列順序仍須一致。\n\n"
                "規則會固定保存於案例版本，試做失敗不會自動放寬。",
                initialvalue=1,
                minvalue=1,
                maxvalue=2,
                parent=self.root,
            )
            if rule is None:
                return
            self.model.add_case(
                source,
                expected,
                outputs[selected - 1] if outputs else None,
                comparison="exact_bytes" if rule == 1 else "json_equal",
            )
            self.refresh_project()

    def manage_cases(self):
        from .cases_dialog import open_cases

        open_cases(self)

    def version_history(self):
        from .history_dialog import open_history

        open_history(self)

    def verify(self):
        self.status.set("正在準備候選包與試做條件……")
        self.tabs.select(2)
        self._background(self.model.prepare_verification, self._verify_prepared)

    def _verify_prepared(self, prepared):
        authorization = None
        if prepared["models"]:
            destinations = "\n".join(f"{m['model']} → {m['endpoint']}" for m in prepared["models"])
            sources = "\n".join(item["name"] for item in prepared["sources"])
            if not messagebox.askyesno(
                "確認 LLM 試做範圍",
                f"本次會將固定測試輸入送至：\n{destinations}\n\n案例：\n{sources}\n\n保守預算上限 USD {prepared['maximum_budget_micro_usd'] / 1_000_000:.2f}。是否開始？",
                parent=self.root,
            ):
                return
            authorization = self.model.authorize_verification(prepared)
        self.status.set("正在隔離工作區試做，核對實際成果與成功案例……")
        self.tabs.select(2)

        self._background(lambda: self.model.verify(authorization=authorization), self.show_report)

    def show_report(self, report):
        self.last_report = report
        passed = report["status"] == "passed"
        lines = [
            "試做通過" if passed else "試做未通過",
            f"已檢查 {len(report['cases'])} 個固定案例。",
        ]
        for item in report["cases"]:
            lines.append("✓ 成果相符" if item["status"] == "passed" else "× 成果不符或執行失敗")
            if "difference" in item:
                lines.append(
                    f"預期 {item['difference']['expected_bytes']} bytes；實際 {item['difference']['actual_bytes']} bytes。"
                )
                lines.append(item["difference"].get("reason", ""))
                lines.append(item["difference"].get("text_diff", ""))
        if report.get("message"):
            lines.append(report["message"])
        self._set_text(self.report_text, "\n\n".join(lines))
        self.status.set(
            "試做通過，請核對成果後確認發布。" if passed else "未通過驗收的候選包不能發布。"
        )

    def publish(self):
        if not self.reviewed.get() or not self.actor_var.get().strip():
            raise StudioError("APPROVAL_REQUIRED", "請填寫核准者，並勾選已核對成果與目標。")
        target = filedialog.askdirectory(
            title="選擇要安裝流程的 RoutineKeel 工作區", parent=self.root
        )
        if target:
            actor = self.actor_var.get()
            self._background(
                lambda: self.model.approve_publish(target, actor, "已核對試做成果與工作目標"),
                lambda result: self.status.set("已發布到本機工作區；舊版本與原工作成果保留。"),
            )

    def repair(self):
        if not self.last_report:
            raise StudioError("TEST_FAILED", "請先完成試做。")
        report_id = self.last_report["report_id"]
        self.status.set("正在準備修復資料範圍……")
        self._background(lambda: self.model.prepare_repair(report_id), self._repair_prepared)

    def _repair_prepared(self, prepared):
        scope = json.loads(prepared.scope_json)
        cases = "\n".join(
            f"• {s['specification']['name']}（{s['specification']['comparison']}）"
            for s in scope["cases"]
        )
        runtime = scope["runtime"]
        runtime_text = ""
        if runtime["models"]:
            runtime_text = (
                "\n\n試做也會將案例及修復流程衍生資料送至：\n"
                + "\n".join(f"{m['model']} → {m['endpoint']}" for m in runtime["models"])
                + f"\n兩輪試做預算上限 USD {scope['maximum_runtime_budget_micro_usd'] / 1_000_000:.2f}。"
            )
        if not messagebox.askyesno(
            "確認修復資料與預算",
            f"修復模型：{scope['model']}\n目的端：{scope['destination']}\n\n"
            "會傳送目前流程（含程式與證據）、以下案例的完整輸入與正確成果，以及試做差異。"
            "最多兩輪；後續也會傳送修復提案及重測差異。\n\n"
            + cases
            + f"\n\n設計期累計預算上限 USD {scope['budget']['budget_micro_usd'] / 1_000_000:.2f}。"
            + runtime_text
            + "\n\n案例與規則保持固定；不會自動發布。是否開始？",
            parent=self.root,
        ):
            self.status.set("尚未授權修復，未傳送資料。")
            return
        secrets = self.model.authorize_verification(runtime).secrets if runtime["models"] else None
        self.last_report = None
        self.reviewed.set(False)
        self.tabs.select(1)
        self.status.set("正在修復；可停止後續模型呼叫，已開始的試做會先完成。")

        def work():
            try:
                return self.model.repair_model(
                    prepared,
                    lambda event: self.messages.put(("event", None, event)),
                    self.cancelled.is_set,
                    authorized_hash=prepared.request_hash,
                    runtime_secrets=secrets,
                ), None
            except StudioError as exc:
                return None, exc

        def done(result):
            report, error = result
            self.refresh_project()
            if error:
                self.error(error)
                return
            self.last_report = report
            self.tabs.select(2)
            self.show_report(report)
            self.status.set(
                "修復後試做通過，仍需人工核對與核准。"
                if report["status"] == "passed"
                else "兩輪修復已結束，仍未通過；請查看分析與失敗報告。"
            )

        self._background(work, done)
        self.stop_button.configure(state="normal")

    def release_history(self):
        rows = [r for r in self.model.release_history() if r["status"] == "published"]
        window = tk.Toplevel(self.root)
        self.prepare_dialog(window)
        window.title("發布歷史與版本回復")
        listing = tk.Listbox(window, width=80, height=12)
        listing.pack(padx=12, pady=12)
        for row in rows:
            listing.insert("end", f"{row['created_at']} · {row['build_id'][-8:]}")

        def restore():
            if listing.curselection() and messagebox.askyesno(
                "回復版本", "將切換到此成功版本，保留既有成果。", parent=window
            ):
                release_id = rows[listing.curselection()[0]]["release_id"]
                window.destroy()
                self._background(
                    lambda: self.model.rollback(release_id),
                    lambda _: self.status.set("已回復選取版本。"),
                )

        ttk.Button(window, text="回復選取版本", command=restore).pack(pady=8)

    def configure_model(self):
        if not self.confirm_leave():
            return
        window = tk.Toplevel(self.root)
        self.prepare_dialog(window)
        window.title("模型與用量設定（金鑰只保留於本次工作台）")
        values = {}
        saved = self.model.session_config
        fields = [
            (
                "endpoint",
                "API 端點",
                saved.get("endpoint", "https://api.openai.com/v1/chat/completions"),
            ),
            ("model", "模型名稱", saved.get("model", "")),
            ("key", "API 金鑰（不寫入專案）", ""),
            ("budget", "此專案設計期預算 USD", saved.get("budget_usd", "0.25")),
            ("input", "每百萬輸入 token 費率 USD", saved.get("input_rate", "")),
            ("output", "每百萬輸出 token 費率 USD", saved.get("output_rate", "")),
            (
                "runtime_budget",
                "每次流程執行的模型預算上限 USD",
                saved.get("runtime_budget_usd", "0.25"),
            ),
        ]
        for name, label, default in fields:
            ttk.Label(window, text=label).pack(anchor="w", padx=12, pady=(8, 0))
            variable = tk.StringVar(value=default)
            values[name] = variable
            ttk.Entry(
                window, textvariable=variable, width=65, show="•" if name == "key" else ""
            ).pack(padx=12)
        runtime_enabled = tk.BooleanVar(
            value=self.model.session_config.get("runtime_enabled", False)
        )
        ttk.Checkbutton(
            window, text="允許含執行期 AI 的候選流程（試做前仍需確認）", variable=runtime_enabled
        ).pack(anchor="w", padx=12, pady=8)

        def save():
            try:
                if not values["model"].get().strip():
                    raise StudioError("INPUT_INVALID", "請填寫模型名稱。")
                try:
                    amounts = {
                        key: float(values[key].get())
                        for key in ("budget", "input", "output", "runtime_budget")
                    }
                except ValueError as exc:
                    raise StudioError(
                        "INPUT_INVALID", "預算與費率請填入數字，例如 0.25；請勿留白。"
                    ) from exc
                self.model.configure_provider(
                    values["endpoint"].get().strip(),
                    values["model"].get().strip(),
                    amounts["budget"],
                    amounts["input"],
                    amounts["output"],
                    runtime_enabled=runtime_enabled.get(),
                    runtime_budget_usd=amounts["runtime_budget"],
                )
                if values["key"].get():
                    os.environ[self.model.provider.api_key_env] = values["key"].get()
                window.destroy()
                if self.model.project_id:
                    self.refresh_project()
                self.status.set("模型已設定。每次送出前仍會顯示來源與預算供你確認。")
            except Exception as exc:
                self.error(exc)

        ttk.Label(
            window,
            text="請填入已查核的模型費率。金鑰只保留在本次工作台，送出資料前仍會再次確認範圍。",
            wraplength=500,
        ).pack(padx=12, pady=8)
        controls = ttk.Frame(window)
        controls.pack(fill="x", padx=12, pady=(4, 12))
        ttk.Button(controls, text="儲存設定", command=save, style="Primary.TButton").pack(
            side="right"
        )
        ttk.Button(controls, text="取消", command=window.destroy).pack(side="right", padx=8)

    def analyze(self):
        if not self.selected_source:
            raise StudioError("SOURCE_NOT_FOUND", "請先選擇要分析的來源。")
        prepared = self.model.prepare_analysis([self.selected_source])
        if self.model.provider.external and not messagebox.askyesno(
            "確認模型資料範圍",
            f"目的端：{prepared.destination}\n模型：{prepared.model}\n將傳送選取來源的文字、目標與工具契約，並可在同一範圍內修正最多 {prepared.repair_limit} 輪。\n設計期總預算上限：USD {self.model.budget.budget_micro_usd / 1_000_000:.2f}\n繼續傳送？",
            parent=self.root,
        ):
            return
        self.raw_analysis = ""
        self._set_text(self.analysis, "")
        self.tabs.select(1)
        self.status.set("正在分析；文字會逐段顯示，完成驗證後才保存候選流程。")

        def done(_):
            self.refresh_project()
            self.status.set("分析已保存；請檢查流程或回答待確認問題。")
            revision = self.model.revision()
            if revision and revision.ir.get("questions"):
                self._set_text(
                    self.analysis,
                    analysis_text(self.raw_analysis)
                    + "\n\n待確認：\n"
                    + "\n".join(revision.ir["questions"]),
                )

        self._background(
            lambda: self.model.analyze(
                prepared,
                lambda event: self.messages.put(("event", None, event)),
                self.cancelled.is_set,
            ),
            done,
        )
        self.stop_button.configure(state="normal")

    def stop(self):
        self.cancelled.set()
        self.status.set("已要求停止，等待模型連線回應；尚未宣告完成。")

    def close(self):
        if self.busy:
            self.close_requested = True
            self.stop()
            self.status.set("目前操作結束後會關閉；上一個有效版本保留。")
        elif self.confirm_leave():
            self.root.destroy()

    def prepare_dialog(self, window):
        window.configure(bg="#FFFFFF")
        window.transient(self.root)
        window.grab_set()
        window.bind("<Escape>", lambda _event: window.destroy())

    def run(self):
        self.root.mainloop()
