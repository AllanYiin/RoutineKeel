"""Small Tk desktop window; all workflow operations use LocalUIModel/WorkflowService."""
from __future__ import annotations

import math
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .model import LocalUIModel, error_card, preview_file

COLORS = {'background': '#F7F9FC', 'card': '#FFFFFF', 'text': '#172337',
          'muted': '#516074', 'accent': '#2864DC', 'success': '#167A53',
          'warning': '#A96900', 'error': '#BD3B43'}
TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted'}


class DesktopApp:
    def __init__(self, project_path, *, root=None):
        self.root = root or tk.Tk()
        self.root.title('RoutineKeel｜本機工作台')
        self.root.geometry('1180x760')
        self.root.configure(background=COLORS['background'])
        self.model = LocalUIModel(project_path)
        self.messages = queue.Queue()
        self.selected_task = None
        self.preview = None
        self.original_image = self.display_image = None
        self.last_status = None
        self._build()
        self._refresh_workflows()
        self._refresh_history()
        self.root.after(100, self._drain_messages)
        self.root.after(1000, self._poll_run)

    def _build(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill='x')
        ttk.Label(top, text='RoutineKeel 本機工作台', font=('Segoe UI', 17, 'bold')).pack(side='left')
        ttk.Button(top, text='開啟專案', command=self._choose_project).pack(side='right')
        self.project_label = ttk.Label(top, text=str(self.model.project_path), foreground=COLORS['muted'])
        self.project_label.pack(side='right', padx=12)

        panes = ttk.Panedwindow(self.root, orient='horizontal')
        panes.pack(fill='both', expand=True, padx=10, pady=(0, 10))
        left = ttk.Frame(panes, padding=8)
        center = ttk.Frame(panes, padding=8)
        right = ttk.Frame(panes, padding=8)
        panes.add(left, weight=2)
        panes.add(center, weight=4)
        panes.add(right, weight=3)

        ttk.Label(left, text='工作流', font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        self.workflow_list = tk.Listbox(left, height=9, exportselection=False)
        self.workflow_list.pack(fill='x', pady=(6, 12))
        self.workflow_list.bind('<<ListboxSelect>>', self._select_workflow)
        ttk.Label(left, text='歷史執行', font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        self.history_list = tk.Listbox(left, exportselection=False)
        self.history_list.pack(fill='both', expand=True, pady=(6, 8))
        self.history_list.bind('<<ListboxSelect>>', self._select_history)
        ttk.Button(left, text='重新讀取歷史', command=self._refresh_history).pack(fill='x')

        ttk.Label(center, text='輸入檔案', font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        source_row = ttk.Frame(center)
        source_row.pack(fill='x', pady=5)
        self.source_var = tk.StringVar()
        ttk.Entry(source_row, textvariable=self.source_var).pack(side='left', fill='x', expand=True)
        ttk.Button(source_row, text='選檔', command=self._choose_source).pack(side='left', padx=(5, 0))
        ttk.Label(center, text='檔案預覽', font=('Segoe UI', 10, 'bold')).pack(anchor='w')
        self.preview_label = ttk.Label(center, text='尚未選擇檔案', foreground=COLORS['muted'])
        self.preview_label.pack(anchor='w', pady=(3, 5))
        self.preview_area = ttk.Frame(center, height=200)
        self.preview_area.pack(fill='x')
        self.preview_area.pack_propagate(False)
        self.image_label = ttk.Label(self.preview_area)
        self.image_label.pack(fill='both', expand=True)
        self.text_preview = tk.Text(self.preview_area, height=9, wrap='word', state='disabled')
        self.zoom_var = tk.IntVar(value=100)
        zoom_row = ttk.Frame(center)
        zoom_row.pack(fill='x')
        ttk.Label(zoom_row, text='圖片縮放').pack(side='left')
        self.zoom = ttk.Scale(zoom_row, from_=25, to=200, variable=self.zoom_var,
                              command=lambda _value: self._render_image())
        self.zoom.pack(side='left', fill='x', expand=True, padx=8)

        ttk.Label(center, text='成果位置', font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=(8, 0))
        output_row = ttk.Frame(center)
        output_row.pack(fill='x', pady=5)
        self.output_var = tk.StringVar()
        ttk.Entry(output_row, textvariable=self.output_var).pack(side='left', fill='x', expand=True)
        ttk.Button(output_row, text='選資料夾', command=self._choose_output).pack(side='left', padx=(5, 0))

        settings_row = ttk.Frame(center)
        settings_row.pack(fill='x', pady=(7, 3))
        ttk.Label(settings_row, text='設定 JSON', font=('Segoe UI', 10, 'bold')).pack(side='left')
        self.offline_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(settings_row, text='離線模式', variable=self.offline_var).pack(side='right')
        self.settings = tk.Text(center, height=5, wrap='word')
        self.settings.insert('1.0', '{}')
        self.settings.pack(fill='x')
        actions = ttk.Frame(center)
        actions.pack(fill='x', pady=8)
        self.plan_button = ttk.Button(actions, text='預先檢查', command=self._plan)
        self.plan_button.pack(side='left')
        self.start_button = ttk.Button(actions, text='開始', command=self._start, state='disabled')
        self.start_button.pack(side='left', padx=5)
        self.cancel_button = ttk.Button(actions, text='取消', command=self._cancel, state='disabled')
        self.cancel_button.pack(side='left')
        ttk.Label(center, text='計畫與進度', font=('Segoe UI', 10, 'bold')).pack(anchor='w')
        self.progress_label = ttk.Label(center, text='尚未開始', foreground=COLORS['muted'])
        self.progress_label.pack(anchor='w', pady=4)
        self.plan_text = tk.Text(center, height=10, wrap='word', state='disabled')
        self.plan_text.pack(fill='both', expand=True)

        ttk.Label(right, text='已完成成果', font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        self.artifacts_list = tk.Listbox(right, height=10)
        self.artifacts_list.pack(fill='x', pady=(6, 12))
        ttk.Label(right, text='錯誤與診斷', font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        self.error_text = tk.Text(right, height=14, wrap='word', state='disabled')
        self.error_text.pack(fill='both', expand=True, pady=(6, 8))
        self.export_button = ttk.Button(right, text='匯出已遮罩診斷',
                                        command=self._export_diagnostics, state='disabled')
        self.export_button.pack(fill='x')

    @staticmethod
    def _write(box, value):
        box.configure(state='normal')
        box.delete('1.0', 'end')
        box.insert('1.0', value)
        box.configure(state='disabled')

    def _error(self, exc=None, diagnostic=None):
        card = error_card(diagnostic, exception=exc)
        names = {'confirmed': '已確認', 'suspected': '疑似', 'unknown': '未知'}
        self._write(self.error_text, '\n'.join([
            card['title'], '階段：' + str(card['stage']),
            '原因確定程度：' + names.get(card['cause_status'], str(card['cause_status'])),
            card['message'], '下一步：' + card['next_action'],
            '錯誤編號：' + (card['error_id'] or '尚未建立')]))

    def _choose_project(self):
        path = filedialog.askdirectory(parent=self.root, title='開啟 RoutineKeel 專案')
        if not path:
            return
        try:
            self.model.open_project(path)
            self.selected_task = None
            self.source_var.set('')
            self.output_var.set('')
            self.start_button.configure(state='disabled')
            self.cancel_button.configure(state='disabled')
            self.export_button.configure(state='disabled')
            self.last_status = None
            self.project_label.configure(text=str(self.model.project_path))
            self._refresh_workflows()
            self._refresh_history()
            self._write(self.error_text, '')
            self._write(self.plan_text, '')
            self.progress_label.configure(text='已開啟專案；請選擇工作流與輸入。')
        except Exception as exc:
            self._error(exc)

    def _refresh_workflows(self):
        self.workflow_list.delete(0, 'end')
        try:
            self.available = self.model.workflows()
            for item in self.available:
                self.workflow_list.insert('end', f"{item['title']}（{item['id']}）")
        except Exception as exc:
            self.available = []
            self._error(exc)

    def _select_workflow(self, _event=None):
        selection = self.workflow_list.curselection()
        self.selected_task = self.available[selection[0]]['id'] if selection else None
        self.start_button.configure(state='disabled')

    def _choose_source(self):
        path = filedialog.askopenfilename(parent=self.root, title='選擇輸入檔案')
        if path:
            self.source_var.set(path)
            self._show_preview(path)

    def _show_preview(self, path):
        self.image_label.configure(image='', text='')
        self.original_image = self.display_image = None
        self.text_preview.pack_forget()
        try:
            self.preview = preview_file(path)
            item = self.preview
            self.preview_label.configure(text=f'{item.name}｜{item.size_bytes:,} bytes｜{item.extension or "無副檔名"}')
            if item.kind == 'image':
                try:
                    self.original_image = tk.PhotoImage(file=item.path)
                    self._render_image()
                except tk.TclError:
                    self.image_label.configure(text='無法預覽內容：圖片解碼失敗')
            elif item.kind == 'text':
                self._write(self.text_preview, item.text or '')
                self.text_preview.pack(fill='both', expand=True)
            else:
                self.image_label.configure(text='無法預覽內容：' + (item.reason or '格式不支援'))
        except Exception as exc:
            self.preview = None
            self.preview_label.configure(text='無法讀取檔案')
            self._error(exc)

    def _render_image(self):
        if self.original_image is None:
            return
        original = self.original_image
        measured = self.preview_area.winfo_width()
        box_width = measured if measured >= 100 else 320
        box_height = 190
        factor = max(1, math.ceil(max(original.width() / box_width,
                                      original.height() / box_height)))
        scaled = original.subsample(factor, factor)
        zoom = int(self.zoom_var.get())
        if zoom >= 100:
            scaled = scaled.zoom(max(1, round(zoom / 100)))
        elif zoom <= 50:
            scaled = scaled.subsample(max(1, round(100 / zoom)))
        self.display_image = scaled
        self.image_label.configure(image=scaled)

    def _choose_output(self):
        path = filedialog.askdirectory(parent=self.root, title='選擇成果資料夾')
        if path:
            self.output_var.set(path)

    def _background(self, name, action):
        self.plan_button.configure(state='disabled')
        self.start_button.configure(state='disabled')
        def work():
            try:
                self.messages.put((name, action(), None))
            except Exception as exc:
                self.messages.put((name, None, exc))
        threading.Thread(target=work, name=f'ui-{name}', daemon=True).start()

    def _plan(self):
        if not self.selected_task:
            self._error(ValueError('請先選擇工作流'))
            return
        self.progress_label.configure(text='正在檢查計畫…')
        task, source, output = self.selected_task, self.source_var.get(), self.output_var.get()
        settings, offline = self.settings.get('1.0', 'end'), self.offline_var.get()
        self._background('plan', lambda: self.model.plan(
            task, source, output, settings, offline))

    def _start(self):
        self.progress_label.configure(text='正在提交工作…')
        self._background('start', self.model.start)

    def _cancel(self):
        try:
            response = self.model.cancel()
            self.progress_label.configure(text='取消已請求；等待執行程序停止。' if not response['confirmed_stopped']
                                          else '已確認取消')
        except Exception as exc:
            self._error(exc)

    def _drain_messages(self):
        while True:
            try:
                name, result, exc = self.messages.get_nowait()
            except queue.Empty:
                break
            self.plan_button.configure(state='normal')
            if exc:
                self.progress_label.configure(text='操作未完成')
                self._error(exc)
                continue
            if name == 'plan':
                plan = result.plan
                blocked = plan.get('blocked') or []
                self._write(self.plan_text, f"流程：{plan.get('workflow')}\n成果：{plan.get('output')}\n"
                            f"阻礙：{blocked if blocked else '無'}\n"
                            f"提醒：{plan.get('warnings') or '無'}\n估算：{result.estimates_status}")
                self.progress_label.configure(text='預先檢查完成' if not blocked else '預先檢查發現阻礙')
                ready = bool(plan.get('ready_to_prepare') and result.execution_spec_hash and not blocked)
                self.start_button.configure(state='normal' if ready else 'disabled')
            elif name == 'start':
                self.progress_label.configure(text=f'已提交 {result.run_id}；狀態：{result.status}')
                self.cancel_button.configure(state='normal')
                self.export_button.configure(state='normal')
                self._refresh_history()
        self.root.after(100, self._drain_messages)

    def _poll_run(self):
        run_id = self.model.run_id
        if run_id:
            try:
                status = self.model.status(run_id)
                state = status['execution_status']
                if state != self.last_status:
                    self.last_status = state
                    events = self.model.service.list_run_events(run_id, limit=10)['events']
                    latest = events[-1]['name'] if events else '尚無事件'
                    self.progress_label.configure(text=f'Run {run_id}｜{state}｜最後事件：{latest}')
                    if state in TERMINAL:
                        self.cancel_button.configure(state='disabled')
                        self._show_run_details(run_id)
                        self._refresh_history()
            except Exception as exc:
                self._error(exc)
        self.root.after(1000, self._poll_run)

    def _refresh_history(self):
        self.history_list.delete(0, 'end')
        try:
            self.history = self.model.history()
            for row in self.history:
                self.history_list.insert('end', f"{row.get('run_id')}｜{row.get('status')}")
        except Exception as exc:
            self.history = []
            self._error(exc)

    def _select_history(self, _event=None):
        selection = self.history_list.curselection()
        if not selection:
            return
        run_id = self.history[selection[0]]['run_id']
        self.model.run_id = run_id
        self.last_status = None
        self.export_button.configure(state='normal')
        self._show_run_details(run_id)

    def _show_run_details(self, run_id):
        try:
            status = self.model.status(run_id)
            self.cancel_button.configure(state='disabled' if status['execution_status'] in TERMINAL
                                         else 'normal')
            diagnostic = self.model.diagnostics(run_id)
            self.artifacts_list.delete(0, 'end')
            for artifact in self.model.artifacts(run_id):
                self.artifacts_list.insert('end', f"{artifact['path']}｜{artifact['size_bytes']:,} bytes")
            if status['execution_status'] in {'failed', 'interrupted', 'cancelled'}:
                self._error(diagnostic=diagnostic)
            else:
                self._write(self.error_text, f"Run：{run_id}\n狀態：{status['execution_status']}\n"
                            '沒有已確認的執行錯誤。')
        except Exception as exc:
            self._error(exc)

    def _export_diagnostics(self):
        if not self.model.run_id:
            return
        path = filedialog.asksaveasfilename(parent=self.root, title='匯出已遮罩診斷',
                                            defaultextension='.json', filetypes=[('JSON', '*.json')])
        if not path or not messagebox.askyesno('確認匯出', '只匯出已遮罩診斷至所選檔案？', parent=self.root):
            return
        try:
            self.model.export_diagnostics(path)
            messagebox.showinfo('匯出完成', '已保存已遮罩診斷。', parent=self.root)
        except Exception as exc:
            self._error(exc)

    def run(self):
        self.root.mainloop()


def run_ui(project_path):
    DesktopApp(project_path).run()
