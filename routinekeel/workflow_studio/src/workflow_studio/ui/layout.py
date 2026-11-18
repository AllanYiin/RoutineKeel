"""Responsive native layout, keeping task actions separate from preview space."""

import tkinter as tk
from tkinter import ttk

from .widgets import BoundText, ScrollColumn


def fit_wrapped_labels(parent, minimum_width):
    for widget in parent.winfo_children():
        if (
            isinstance(widget, ttk.Label)
            and widget.winfo_pixels(widget.cget("wraplength") or 0) > 0
        ):
            widget.bind(
                "<Configure>",
                lambda event, label=widget: label.configure(
                    wraplength=max(minimum_width, event.width)
                ),
                add=True,
            )
        fit_wrapped_labels(widget, minimum_width)


def build_workbench(app):
    root, family = app.root, app.display["font_family"]

    def s(value):
        return round(value * app.display["scale"])

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", font=(family, 11))
    style.configure("TFrame", background="#FFFFFF")
    style.configure("Header.TFrame", background="#F8FAFC")
    style.configure("TLabel", background="#FFFFFF", foreground="#0F172A")
    style.configure("Header.TLabel", background="#F8FAFC", foreground="#475569")
    style.configure("TLabelframe", background="#FFFFFF", bordercolor="#CBD5E1", relief="solid")
    style.configure(
        "TLabelframe.Label", background="#FFFFFF", foreground="#0F172A", font=(family, 12, "bold")
    )
    style.configure("TCheckbutton", background="#FFFFFF", foreground="#334155")
    style.configure(
        "TButton",
        padding=(s(10), s(7)),
        background="#FFFFFF",
        foreground="#334155",
        bordercolor="#CBD5E1",
        focusthickness=1,
    )
    style.map(
        "TButton",
        background=[("active", "#EFF6FF"), ("disabled", "#F1F5F9")],
        foreground=[("disabled", "#94A3B8")],
    )
    style.configure(
        "Primary.TButton", background="#2563EB", foreground="#FFFFFF", bordercolor="#2563EB"
    )
    style.map(
        "Primary.TButton",
        background=[("disabled", "#E2E8F0"), ("active", "#1D4ED8")],
        foreground=[("disabled", "#64748B"), ("!disabled", "#FFFFFF")],
    )
    style.configure("TEntry", padding=s(5), fieldbackground="#FFFFFF", bordercolor="#CBD5E1")
    style.configure("TCombobox", padding=s(4), fieldbackground="#FFFFFF", bordercolor="#CBD5E1")
    style.map("TCombobox", fieldbackground=[("readonly", "#FFFFFF")])
    style.configure(
        "Treeview",
        rowheight=s(29),
        background="#FFFFFF",
        fieldbackground="#FFFFFF",
        foreground="#334155",
        bordercolor="#CBD5E1",
    )
    style.map(
        "Treeview", background=[("selected", "#DBEAFE")], foreground=[("selected", "#1E3A8A")]
    )
    style.configure("TNotebook", background="#FFFFFF", borderwidth=0)
    style.configure("TNotebook.Tab", padding=(s(9), s(8)), background="#F1F5F9")
    style.map(
        "TNotebook.Tab", background=[("selected", "#FFFFFF")], foreground=[("selected", "#1D4ED8")]
    )

    header = ttk.Frame(root, padding=(s(18), s(12)), style="Header.TFrame")
    header.pack(fill="x")
    ttk.Label(header, text="RoutineKeel", font=(family, 20, "bold"), style="Header.TLabel").pack(
        side="left"
    )
    ttk.Label(header, text="工作流程製作工作台", font=(family, 12), style="Header.TLabel").pack(
        side="left", padx=s(16)
    )
    app.advanced_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        header, text="顯示進階操作", variable=app.advanced_var, command=app.refresh_actions
    ).pack(side="right", padx=s(8))
    app.button(header, "模型設定", app.configure_model, side="right")
    app.button(header, "工具管理", app.manage_tools, side="right", padx=s(6), advanced=True)
    app.button(header, "切換工作區", app.choose_workspace, side="right", padx=s(6))
    app.status = tk.StringVar(value="先建立工作專案，再提供工作說明或成功案例。資料預設留在本機。")
    app.status_label = ttk.Label(
        root, textvariable=app.status, style="Header.TLabel", padding=(s(18), 0, s(18), s(12))
    )
    app.status_label.pack(fill="x")
    guidance = ttk.Frame(root, padding=(s(18), 0, s(18), s(10)), style="Header.TFrame")
    guidance.pack(fill="x")
    app.guidance_title, app.guidance_detail, app.guidance_feedback = (
        tk.StringVar(),
        tk.StringVar(),
        tk.StringVar(),
    )
    app.next_button = app.button(
        guidance, "開始", app.advance, style="Primary.TButton", side="right", padx=(s(12), 0)
    )
    explanation = ttk.Frame(guidance, style="Header.TFrame")
    explanation.pack(side="left", fill="both", expand=True)
    ttk.Label(
        explanation,
        textvariable=app.guidance_title,
        style="Header.TLabel",
        font=(family, 12, "bold"),
    ).pack(anchor="w")
    detail = ttk.Label(
        explanation, textvariable=app.guidance_detail, style="Header.TLabel", wraplength=s(750)
    )
    detail.pack(anchor="w", fill="x", pady=(s(3), 0))
    feedback = ttk.Label(
        explanation,
        textvariable=app.guidance_feedback,
        style="Header.TLabel",
        foreground="#92400E",
        wraplength=s(750),
    )
    feedback.pack(anchor="w", fill="x")
    explanation.bind(
        "<Configure>",
        lambda event: (
            detail.configure(wraplength=max(s(250), event.width)),
            feedback.configure(wraplength=max(s(250), event.width)),
        ),
    )
    app.workspace_label = ttk.Label(
        root,
        text=f"工作區：{app.model.workspace}",
        style="Header.TLabel",
        font=(family, 9),
        padding=(s(18), s(4)),
    )
    app.workspace_label.pack(side="bottom", fill="x")
    app.panes = ttk.Panedwindow(root, orient="horizontal")
    app.panes.pack(fill="both", expand=True, padx=s(14), pady=(0, s(14)))
    app.columns = [ScrollColumn(app.panes, padding=s(12)) for _ in range(2)]
    for column, weight in zip(app.columns, (3, 5)):
        app.panes.add(column, weight=weight)
    left, center = [column.body for column in app.columns]
    right = ttk.Frame(app.panes, padding=s(12))
    app.panes.add(right, weight=4)

    def keep_panes_usable(_event=None):
        width = app.panes.winfo_width()
        left_min, center_min, right_min = s(280), s(300), s(300)
        if width < left_min + center_min + right_min:
            return
        first = min(max(app.panes.sashpos(0), left_min), width - center_min - right_min)
        second = min(max(app.panes.sashpos(1), first + center_min), width - right_min)
        app.panes.sashpos(0, first)
        app.panes.sashpos(1, second)

    app.panes.bind("<Configure>", keep_panes_usable, add=True)
    app.panes.bind("<ButtonRelease-1>", keep_panes_usable, add=True)

    left.columnconfigure(0, weight=1)
    left.rowconfigure(4, weight=1)
    project_header = ttk.Frame(left)
    project_header.grid(row=0, column=0, sticky="ew")
    ttk.Label(project_header, text="工作專案", font=(family, 12, "bold")).pack(side="left")
    app.button(project_header, "＋ 新專案", app.start_new_project, side="right")
    app.project_list = tk.Listbox(
        left,
        width=1,
        height=4,
        exportselection=False,
        relief="flat",
        highlightthickness=1,
        highlightbackground="#CBD5E1",
        selectbackground="#DBEAFE",
        selectforeground="#1E3A8A",
        activestyle="none",
    )
    app.project_list.grid(row=1, column=0, sticky="ew", pady=s(8))
    app.project_list.bind("<<ListboxSelect>>", app._select_project)
    project_controls = ttk.Frame(left)
    project_controls.grid(row=2, column=0, sticky="ew")
    app.defer_widget(project_controls, advanced=True)
    app.archived = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        project_controls, text="顯示已封存", variable=app.archived, command=app.refresh_projects
    ).pack(anchor="w")
    row = ttk.Frame(project_controls)
    row.pack(fill="x", pady=(s(5), s(10)))
    app.button(
        row,
        "封存",
        lambda: app._archive(False),
        requires="active",
        side="left",
        expand=True,
        fill="x",
    )
    app.button(
        row,
        "恢復",
        lambda: app._archive(True),
        requires="archived",
        side="left",
        expand=True,
        fill="x",
        padx=(s(5), 0),
    )
    imports = ttk.Frame(left)
    imports.grid(row=3, column=0, sticky="ew")
    ttk.Separator(imports).pack(fill="x", pady=(0, s(10)))
    ttk.Label(imports, text="來源與成功案例", font=(family, 12, "bold")).pack(anchor="w")
    row = ttk.Frame(imports)
    row.pack(fill="x", pady=s(7))
    for label, action in (
        ("檔案", app.import_file),
        ("資料夾", app.import_folder),
        ("貼上文字", app.paste_source),
    ):
        app.button(
            row,
            label,
            action,
            requires="active",
            side="left",
            expand=True,
            fill="x",
            padx=(0, s(3)),
        )
    tree_frame = ttk.Frame(left)
    tree_frame.grid(row=4, column=0, sticky="nsew")
    app.source_tree = ttk.Treeview(tree_frame, show="tree", height=4, selectmode="extended")
    scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=app.source_tree.yview)
    scroll.pack(side="right", fill="y")
    app.source_tree.configure(yscrollcommand=scroll.set)
    app.source_tree.pack(side="left", fill="both", expand=True)
    app.source_tree.bind("<<TreeviewSelect>>", app._source_selected)
    actions = ttk.Frame(left)
    actions.grid(row=5, column=0, sticky="ew", pady=(s(7), 0))
    row = ttk.Frame(actions)
    row.pack(fill="x")
    app.defer_widget(row, advanced=True, show_when="sources")
    app.button(
        row,
        "替換來源",
        app.replace_source,
        requires="single_source",
        side="left",
        expand=True,
        fill="x",
    )
    app.button(
        row,
        "移除參照",
        app.remove_source,
        requires="single_source",
        side="left",
        expand=True,
        fill="x",
        padx=(s(5), 0),
    )
    app.button(
        actions,
        "編輯來源說明",
        app.edit_source_description,
        requires="single_source",
        fill="x",
        pady=(s(4), s(5)),
        advanced=True,
        show_when="sources",
    )
    app.button(
        actions,
        "從成功案例找流程",
        app.find_folder_candidates,
        requires="folder",
        fill="x",
        pady=(s(10), s(5)),
        show_when="folder",
    )
    app.button(
        actions,
        "檢查案例關係與版本",
        app.review_folder_relations,
        requires="folder",
        fill="x",
        pady=(0, s(5)),
        advanced=True,
        show_when="folder",
    )
    app.button(
        actions,
        "AI 整理選取來源",
        app.analyze,
        requires="analysis",
        fill="x",
        show_when="sources",
    )
    app.stop_button = ttk.Button(actions, text="停止分析", command=app.stop, state="disabled")
    ttk.Label(
        actions,
        text="可按 Ctrl／Command 多選來源。灰色按鈕點一下會說明原因；更多管理功能在「顯示進階操作」。",
        wraplength=s(270),
        foreground="#64748B",
        font=(family, 10),
    ).pack(fill="x", pady=(s(3), 0))

    goal = ttk.LabelFrame(center, text="1  定義工作目標", padding=s(12))
    goal.pack(fill="x")
    app.name_var, app.goal_var, app.success_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
    ttk.Label(goal, text="專案名稱").pack(anchor="w")
    app.name_entry = ttk.Entry(goal, textvariable=app.name_var)
    app.name_entry.pack(fill="x", pady=(s(3), s(8)))
    for label, variable, attr in (
        ("要完成什麼工作", app.goal_var, "goal_input"),
        ("成功條件（可一行一項）", app.success_var, "success_input"),
    ):
        ttk.Label(goal, text=label).pack(anchor="w")
        field = BoundText(
            goal,
            variable,
            width=1,
            height=2,
            wrap="word",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#CBD5E1",
            highlightcolor="#2563EB",
            padx=s(6),
            pady=s(4),
            undo=True,
        )
        field.pack(fill="x", pady=(s(3), s(8)))
        setattr(app, attr, field)
    row = ttk.Frame(goal)
    row.pack(fill="x")
    app.new_button = app.button(row, "建立專案", app.create_project, requires="new", side="left")
    app.button(row, "儲存目標", app.save_project, requires="editable", side="left", padx=s(6))
    history_row = ttk.Frame(goal)
    history_row.pack(fill="x", pady=(s(6), 0))
    app.button(
        history_row,
        "版本與差異",
        app.version_history,
        requires="project",
        side="left",
        advanced=True,
        show_when="project",
    )
    app.button(
        history_row,
        "比較更新",
        app.compare_project_updates,
        requires="project",
        side="left",
        padx=s(6),
        advanced=True,
        show_when="project",
    )
    app.save_state = tk.StringVar()
    ttk.Label(
        row, textvariable=app.save_state, foreground="#B45309", font=(family, 10), wraplength=s(120)
    ).pack(side="right")
    flow = ttk.LabelFrame(center, text="2  查看與調整流程", padding=s(12))
    flow.pack(fill="both", expand=True, pady=(s(12), 0))
    app.candidate_choice = ttk.Combobox(flow, state="readonly", width=1)
    app.candidate_choice.pack(fill="x")
    app.defer_widget(app.candidate_choice, show_when="candidate")
    app.candidate_choice.bind("<<ComboboxSelected>>", lambda _e: app.refresh_actions())
    app.button(
        flow,
        "採用選取的候選流程",
        app.accept_candidate,
        requires="candidate",
        fill="x",
        pady=s(6),
        show_when="candidate",
    )
    app.button(
        flow,
        "手動建立第一個流程",
        app.initialize_flow,
        requires="initialize",
        fill="x",
        pady=(0, s(6)),
        show_when="manual",
    )
    app.button(
        flow,
        "新增步驟／連線／交付物",
        app.edit_graph,
        requires="workflow",
        fill="x",
        pady=(0, s(6)),
        show_when="workflow",
    )
    app.button(
        flow,
        "業務規則／證據確認",
        app.edit_evidence,
        requires="workflow",
        fill="x",
        pady=(0, s(6)),
        advanced=True,
        show_when="workflow",
    )
    diagram = ttk.Frame(flow)
    diagram.pack(fill="both", expand=True)
    app.canvas = tk.Canvas(
        diagram,
        background="#FFFFFF",
        width=1,
        height=s(150),
        highlightthickness=1,
        highlightbackground="#E2E8F0",
    )
    horizontal = ttk.Scrollbar(diagram, orient="horizontal", command=app.canvas.xview)
    horizontal.pack(side="bottom", fill="x")
    scroll = ttk.Scrollbar(diagram, orient="vertical", command=app.canvas.yview)
    scroll.pack(side="right", fill="y")
    app.canvas.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
    app.canvas.pack(side="left", fill="both", expand=True)
    app.canvas.bind("<Configure>", lambda _e: app.draw_workflow(refresh=False))
    row = ttk.Frame(flow)
    row.pack(fill="x", pady=s(6))
    app.defer_widget(row, show_when="workflow")
    app.button(row, "調整步驟", app.edit_node, requires="node", side="left", expand=True, fill="x")
    app.button(
        row,
        "刪除步驟",
        app.delete_node,
        requires="node",
        side="left",
        expand=True,
        fill="x",
        padx=(s(5), 0),
    )
    app.node_info = tk.StringVar(value="點選流程中的步驟，即可查看輸入、輸出與調整選項。")
    app.button(
        flow,
        "將已試做的自訂處理保存為工具",
        app.save_handler_tool,
        requires="save_handler",
        fill="x",
        pady=(0, s(6)),
        advanced=True,
        show_when="workflow",
    )
    node_help = ttk.Label(
        flow, textvariable=app.node_info, wraplength=s(390), foreground="#64748B", font=(family, 10)
    )
    node_help.pack(fill="x")
    app.defer_widget(node_help, show_when="workflow")

    app.tabs = ttk.Notebook(right)
    app.tabs.pack(fill="both", expand=True)
    tab_columns = [ScrollColumn(app.tabs, padding=s(10)) for _ in range(3)]
    preview, analysis, result = [column.body for column in tab_columns]
    app.columns.append(tab_columns[2])
    for tab, name in zip(tab_columns, ("來源預覽", "分析與疑問", "試做與發布")):
        app.tabs.add(tab, text=name)
    app.preview_label = ttk.Label(
        preview, text="尚未選取來源", wraplength=s(330), font=(family, 11, "bold")
    )
    app.preview_label.pack(fill="x")
    app.image_label = ttk.Label(preview)
    app.image_label.pack(fill="x", pady=s(8))

    def text_area(parent, **kwargs):
        box = tk.Text(
            parent,
            width=1,
            wrap="word",
            relief="flat",
            state="disabled",
            foreground="#334155",
            padx=s(6),
            pady=s(6),
            **kwargs,
        )
        box.pack(fill="both", expand=True)
        return box

    app.preview_text = text_area(preview, height=6)
    app.analysis = text_area(analysis, height=6)
    ttk.Label(result, text="3  先試做，再確認發布", font=(family, 12, "bold")).pack(anchor="w")
    app.button(
        result,
        "選取試做輸入與正確成果",
        app.add_case,
        requires="active",
        fill="x",
        pady=(s(10), s(4)),
    )
    app.case_label = tk.StringVar(value="尚未設定試做案例")
    ttk.Label(result, textvariable=app.case_label, wraplength=s(310), foreground="#64748B").pack(
        fill="x", pady=s(4)
    )
    app.button(
        result,
        "編輯案例／歷史／封存",
        app.manage_cases,
        requires="case_history",
        fill="x",
        pady=s(3),
        advanced=True,
        show_when="case_history",
    )
    app.button(
        result,
        "建置歷史／審核／報告",
        app.manage_builds,
        requires="project",
        fill="x",
        pady=s(3),
        advanced=True,
        show_when="project",
    )
    app.verify_button = app.button(
        result,
        "建立候選包並試做",
        app.verify,
        requires="verify",
        fill="x",
        pady=s(6),
    )
    app.report_text = text_area(result, height=5)
    app.button(
        result,
        "根據試做差異修復（最多兩輪）",
        app.repair,
        requires="repair",
        fill="x",
        pady=s(3),
        show_when="failed_report",
    )
    approval = ttk.Frame(result)
    approval.pack(fill="x", pady=(s(8), 0))
    app.actor_var = tk.StringVar()
    ttk.Label(approval, text="核准者姓名").pack(anchor="w")
    app.actor_entry = ttk.Entry(approval, textvariable=app.actor_var)
    app.actor_entry.pack(fill="x", pady=s(4))
    app.reviewed = tk.BooleanVar(value=False)
    app.review_checkbox = ttk.Checkbutton(
        approval, text="已核對試做成果與工作目標", variable=app.reviewed
    )
    app.review_checkbox.pack(anchor="w", pady=s(5))
    app.button(
        approval,
        "確認並發布到本機工作區",
        app.publish,
        requires="publish",
        fill="x",
    )
    app.button(
        approval,
        "發布歷史／回復舊版",
        app.release_history,
        requires="project",
        fill="x",
        pady=(s(5), 0),
        advanced=True,
        show_when="project",
    )
    app.actor_var.trace_add("write", lambda *_a: app.refresh_actions())
    app.reviewed.trace_add("write", lambda *_a: app.refresh_actions())
    for variable in (app.name_var, app.goal_var, app.success_var):
        variable.trace_add("write", lambda *_a: app.refresh_actions())
    app._set_text(
        app.preview_text,
        "從左側選取檔案，這裡會顯示實際內容。\n\n匯入與預覽都在本機進行；送往模型前會另外確認範圍。",
    )
    app._set_text(app.analysis, "分析過程與需要你確認的問題會顯示在這裡。")
    app._set_text(
        app.report_text,
        "先採用流程並提供正確成果樣本，再進行試做。\n\n只有驗證通過的版本才能核准發布。",
    )

    fit_wrapped_labels(root, s(80))
