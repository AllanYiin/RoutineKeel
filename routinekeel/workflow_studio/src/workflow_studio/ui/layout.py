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

    from .wizard import STEPS, OutputPanels

    app.wizard_step = 0
    header = ttk.Frame(root, padding=(s(20), s(12)), style="Header.TFrame")
    header.pack(fill="x")
    ttk.Label(header, text="RoutineKeel", font=(family, 20, "bold"), style="Header.TLabel").pack(
        side="left"
    )
    app.button(
        header,
        "開啟專案",
        app.open_project_picker,
        side="left",
        padx=s(16),
        show_when="projects_available",
    )
    app.button(header, "新專案", app.start_new_project, side="left", show_when="project")
    app.button(header, "切換工作區", app.choose_workspace, side="left", padx=s(6), advanced=True)
    app.button(header, "模型設定", app.configure_model, side="left", padx=s(6), advanced=True)
    app.button(header, "工具管理", app.manage_tools, side="left", advanced=True)
    app.advanced_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        header, text="進階操作", variable=app.advanced_var, command=app.refresh_actions
    ).pack(side="right")

    steps = ttk.Frame(root, padding=(s(20), 0, s(20), s(10)), style="Header.TFrame")
    steps.pack(fill="x")
    app.step_labels = []
    for i, name in enumerate(STEPS):
        label = ttk.Label(
            steps, text=f"{i + 1}  {name}", style="Header.TLabel", padding=(0, 0, s(25), 0)
        )
        label.pack(side="left")
        app.step_labels.append(label)

    app.status = tk.StringVar(value="直接填寫專案名稱，再建立你的第一個工作流程。")
    app.status_label = ttk.Label(
        root,
        textvariable=app.status,
        style="Header.TLabel",
        padding=(s(20), 0, s(20), s(8)),
        wraplength=s(900),
    )
    app.status_label.pack(fill="x")

    footer = ttk.Frame(root, padding=(s(20), s(10)), style="Header.TFrame")
    footer.pack(side="bottom", fill="x")
    app.guidance_title, app.guidance_detail, app.guidance_feedback = (
        tk.StringVar(),
        tk.StringVar(),
        tk.StringVar(),
    )
    ttk.Label(
        footer, textvariable=app.guidance_detail, style="Header.TLabel", wraplength=s(850)
    ).pack(anchor="w", pady=(0, s(6)))
    ttk.Label(
        footer,
        textvariable=app.guidance_feedback,
        foreground="#92400E",
        style="Header.TLabel",
        wraplength=s(850),
    ).pack(anchor="w")
    navigation = ttk.Frame(footer, style="Header.TFrame")
    navigation.pack(fill="x")
    back_slot = ttk.Frame(navigation, width=s(90), height=s(40), style="Header.TFrame")
    back_slot.pack(side="left")
    back_slot.pack_propagate(False)
    app.back_button = ttk.Button(back_slot, text="上一步", command=app.wizard_back)
    app.next_button = ttk.Button(
        navigation, text="", style="Primary.TButton", command=lambda: app.guard(app.advance)
    )
    app.new_button = app.next_button
    app.stop_button = ttk.Button(navigation, text="停止分析", command=app.stop, state="disabled")
    app.workspace_label = ttk.Label(
        footer, text=f"工作區：{app.model.workspace}", style="Header.TLabel", font=(family, 9)
    )
    app.workspace_label.pack(anchor="w", pady=(s(8), 0))

    app.page_title = tk.StringVar()
    app.page_heading = ttk.Label(
        root, textvariable=app.page_title, font=(family, 16, "bold"), padding=(s(20), s(6))
    )
    app.page_heading.pack(anchor="w")
    container = ttk.Frame(root)
    container.pack(fill="both", expand=True, padx=s(20), pady=s(10))
    app.wizard_pages = [
        ScrollColumn(container, padding=s(12), max_width=s(760 if i in (0, 4) else 1050))
        for i in range(len(STEPS))
    ]
    app.columns = app.wizard_pages
    goal, sources, flow, result, approval = [page.body for page in app.wizard_pages]
    app.wizard_pages[0].pack(fill="both", expand=True)

    # Existing projects are selected in an explicitly named dialog, never an empty input-like box.
    app.project_picker = tk.Toplevel(root)
    app.project_picker.withdraw()
    app.project_picker.title("開啟已儲存專案")
    app.project_picker.protocol("WM_DELETE_WINDOW", app.project_picker.withdraw)
    app.archived = tk.BooleanVar(value=False)
    ttk.Label(app.project_picker, text="選取要開啟的專案", padding=12).pack(anchor="w")
    ttk.Checkbutton(
        app.project_picker, text="顯示已封存", variable=app.archived, command=app.refresh_projects
    ).pack(anchor="w", padx=12)
    app.project_list = tk.Listbox(app.project_picker, height=10, width=45, exportselection=False)
    app.project_list.pack(fill="both", expand=True, padx=12, pady=12)
    app.project_list.bind("<<ListboxSelect>>", lambda _e: app.refresh_actions())
    app.project_list.bind("<Double-Button-1>", app._select_project)
    app.project_list.bind("<Return>", app._select_project)
    app.button(
        app.project_picker,
        "開啟選取專案",
        app._select_project,
        anchor="w",
        padx=12,
        pady=8,
        show_when="picker_selected",
    )

    app.name_var, app.goal_var, app.success_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
    ttk.Label(goal, text="專案名稱").pack(anchor="w")
    app.name_entry = ttk.Entry(goal, textvariable=app.name_var, width=48)
    app.name_entry.pack(fill="x", pady=(s(4), s(14)))
    for label, variable, attr in (
        ("要完成什麼工作", app.goal_var, "goal_input"),
        ("怎樣算完成？（成功條件，可一行一項）", app.success_var, "success_input"),
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
        field.pack(fill="x", pady=(s(4), s(14)))
        setattr(app, attr, field)
    ttk.Label(
        goal,
        text="還沒想完整也能先保存草稿，之後可用「上一步」回來補充。",
        foreground="#64748B",
        wraplength=s(700),
    ).pack(anchor="w")
    app.save_state = tk.StringVar()
    ttk.Label(goal, textvariable=app.save_state, foreground="#92400E").pack(anchor="w", pady=s(6))
    history = ttk.Frame(goal)
    history.pack(fill="x")
    for text, command, rule in (
        ("版本與差異", app.version_history, "project"),
        ("比較更新", app.compare_project_updates, "project"),
        ("封存專案", lambda: app._archive(False), "active"),
        ("恢復專案", lambda: app._archive(True), "archived"),
    ):
        app.button(
            history, text, command, requires=rule, side="left", padx=(0, s(6)), advanced=True
        )

    ttk.Label(sources, text="選擇一種方式加入資料，再確認預覽內容。", wraplength=s(800)).pack(
        anchor="w"
    )
    imports = ttk.Frame(sources)
    imports.pack(fill="x", pady=s(10))
    for text, command in (
        ("貼上文字", app.paste_source),
        ("檔案", app.import_file),
        ("資料夾", app.import_folder),
    ):
        button = app.button(imports, text, command, requires="active", side="left", padx=(0, s(8)))
        if text == "貼上文字":
            app.source_entry_button = button
    source_content = ttk.Frame(sources)
    source_content.pack(fill="both", expand=True)
    app.defer_widget(source_content, show_when="sources")
    listing = ttk.Frame(source_content)
    listing.pack(side="left", fill="y", padx=(0, s(12)))
    ttk.Label(listing, text="已加入的來源（選取以預覽）").pack(anchor="w")
    app.source_tree = ttk.Treeview(listing, show="tree", height=7, selectmode="extended")
    app.source_tree.column("#0", width=s(260), minwidth=s(140))
    app.source_tree.pack(fill="both", expand=True, pady=s(6))
    app.source_tree.bind("<<TreeviewSelect>>", app._source_selected)
    app.source_tree.bind(
        "<ButtonRelease-1>", lambda _e: app.tabs.select(0) if not app.busy else None
    )
    for text, command, rule in (
        ("替換來源", app.replace_source, "single_source"),
        ("移除參照", app.remove_source, "single_source"),
        ("編輯來源說明", app.edit_source_description, "single_source"),
        ("檢查案例關係與版本", app.review_folder_relations, "folder"),
    ):
        app.button(listing, text, command, requires=rule, fill="x", pady=s(3), advanced=True)
    app.button(
        sources,
        "手動建立第一個流程",
        app.initialize_flow,
        requires="initialize",
        anchor="w",
        pady=s(8),
        show_when="manual",
    )
    app.button(
        sources,
        "重新整理來源",
        app.analyze,
        requires="analysis",
        anchor="w",
        pady=s(4),
        show_when="existing_flow",
    )
    app.button(
        sources,
        "從成功案例重新找流程",
        app.find_folder_candidates,
        requires="folder",
        anchor="w",
        pady=s(4),
        show_when="existing_flow",
    )
    app.button(
        sources,
        "設定模型以整理來源",
        app.configure_model,
        anchor="w",
        pady=s(4),
        show_when="needs_model",
    )
    app.button(
        sources,
        "AI 整理選取來源",
        app.analyze,
        requires="analysis",
        anchor="w",
        pady=s(4),
        show_when="empty_folder",
    )

    def text_area(parent, height=8):
        text = tk.Text(
            parent,
            width=1,
            height=height,
            wrap="word",
            relief="flat",
            state="disabled",
            foreground="#334155",
            padx=s(8),
            pady=s(8),
        )
        text.pack(fill="both", expand=True)
        return text

    app.source_tabs = ttk.Notebook(source_content)
    app.source_tabs.pack(side="left", fill="both", expand=True)
    preview, analysis = ttk.Frame(app.source_tabs), ttk.Frame(app.source_tabs)
    app.source_tabs.add(preview, text="來源預覽（唯讀）")
    app.source_tabs.add(analysis, text="分析與待確認問題")
    app.preview_label = ttk.Label(
        preview, text="選取來源後顯示內容", wraplength=s(600), font=(family, 11, "bold")
    )
    app.preview_label.pack(fill="x", pady=s(6))
    app.image_label = ttk.Label(preview)
    app.image_label.pack(fill="x")
    app.preview_text = text_area(preview)
    app.source_analysis = text_area(analysis)
    app.analysis = app.source_analysis

    ttk.Label(flow, text="確認步驟與資料流，再前往試做。", wraplength=s(800)).pack(anchor="w")
    app.candidate_choice = ttk.Combobox(flow, state="readonly", width=1)
    app.candidate_choice.pack(fill="x", pady=s(8))
    app.defer_widget(app.candidate_choice, show_when="candidate")
    app.candidate_choice.bind(
        "<<ComboboxSelected>>", lambda _e: (app.draw_workflow(refresh=False), app.refresh_actions())
    )
    commands = ttk.Frame(flow)
    commands.pack(fill="x", pady=s(6))
    for text, command, rule, advanced in (
        ("新增步驟／連線／交付物", app.edit_graph, "workflow", False),
        ("調整步驟", app.edit_node, "node", False),
        ("刪除步驟", app.delete_node, "node", True),
        ("業務規則／證據確認", app.edit_evidence, "workflow", True),
        ("將已試做的自訂處理保存為工具", app.save_handler_tool, "save_handler", True),
    ):
        app.button(commands, text, command, requires=rule, anchor="w", pady=s(3), advanced=advanced)
    diagram = ttk.Frame(flow)
    diagram.pack(fill="both", expand=True)
    app.canvas = tk.Canvas(
        diagram,
        background="#FFFFFF",
        width=1,
        height=s(270),
        highlightthickness=1,
        highlightbackground="#E2E8F0",
    )
    horizontal = ttk.Scrollbar(diagram, orient="horizontal", command=app.canvas.xview)
    horizontal.pack(side="bottom", fill="x")
    vertical = ttk.Scrollbar(diagram, orient="vertical", command=app.canvas.yview)
    vertical.pack(side="right", fill="y")
    app.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
    app.canvas.pack(side="left", fill="both", expand=True)
    app.canvas.bind("<Configure>", lambda _e: app.draw_workflow(refresh=False))
    app.node_info = tk.StringVar(value="點選步驟可查看輸入、輸出與調整選項。")
    ttk.Label(flow, textvariable=app.node_info, wraplength=s(800)).pack(anchor="w", pady=s(8))

    app.case_button = app.button(
        result, "選取試做輸入與正確成果", app.add_case, requires="active", anchor="w", pady=s(6)
    )
    app.case_label = tk.StringVar(value="尚未設定試做案例")
    ttk.Label(result, textvariable=app.case_label, wraplength=s(800)).pack(anchor="w", pady=s(6))
    app.report_text = text_area(result, height=8)
    app.button(
        result,
        "根據試做差異修復（最多兩輪）",
        app.repair,
        requires="repair",
        anchor="w",
        pady=s(6),
        show_when="failed_report",
    )
    for text, command, rule in (
        ("編輯案例／歷史／封存", app.manage_cases, "case_history"),
        ("建置歷史／審核／報告", app.manage_builds, "project"),
    ):
        app.button(result, text, command, requires=rule, anchor="w", pady=s(3), advanced=True)
    app.repair_panel = ttk.LabelFrame(result, text="修復進度")
    app.repair_analysis = text_area(app.repair_panel, height=6)

    ttk.Label(
        approval, text="試做通過後，請核對實際成果與工作目標，再核准發布。", wraplength=s(800)
    ).pack(anchor="w", pady=(0, s(14)))
    app.actor_var = tk.StringVar()
    ttk.Label(approval, text="核准者姓名").pack(anchor="w")
    app.actor_entry = ttk.Entry(approval, textvariable=app.actor_var, width=40)
    app.actor_entry.pack(fill="x", pady=s(6))
    app.reviewed = tk.BooleanVar(value=False)
    app.review_checkbox = ttk.Checkbutton(
        approval, text="已核對試做成果與工作目標", variable=app.reviewed
    )
    app.review_checkbox.pack(anchor="w", pady=s(12))
    app.button(
        approval,
        "發布歷史／回復舊版",
        app.release_history,
        requires="project",
        anchor="w",
        pady=s(6),
        advanced=True,
    )
    app.tabs = OutputPanels(app)
    for variable in (app.name_var, app.goal_var, app.success_var, app.actor_var, app.reviewed):
        variable.trace_add("write", lambda *_a: app.refresh_actions())
    app._set_text(app.preview_text, "選取左側來源後，這裡會顯示實際內容。")
    app._set_text(app.analysis, "分析與待確認問題會顯示在這裡。")
    app._set_text(app.report_text, "加入實際輸入與正確成果，再按下方「建立候選包並試做」。")
    fit_wrapped_labels(root, s(80))
