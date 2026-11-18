"""Page navigation only; actions retain the existing controller and Service guards."""

STEPS = ("工作目標", "加入來源", "確認流程", "試做", "發布")


def show_page(app, index, *, internal=False):
    context = getattr(app, "wizard_context", {})
    if not internal and (app.busy or not access(context, index)):
        return False
    changed = app.wizard_step != index
    app.wizard_step = index
    for number, page in enumerate(app.wizard_pages):
        if number == index:
            page.pack(fill="both", expand=True)
        else:
            page.pack_forget()
    if index == 1:
        app.analysis = app.source_analysis
    refresh(app, context, getattr(app, "wizard_enabled", {}))
    if changed:
        app.root.after_idle(lambda: focus_page(app))
    return True


def focus_page(app):
    targets = (
        app.name_entry,
        app.source_tree if app.source_rows else app.source_entry_button,
        app.candidate_choice if app.model.candidates else app.canvas,
        app.case_button,
        app.actor_entry,
    )
    target = app.stop_button if app.busy else targets[app.wizard_step]
    if target.winfo_exists() and target.winfo_ismapped():
        target.focus_set()
    else:
        app.page_heading.focus_set()


def access(state, index):
    clean = state.get("project") and not any(
        state.get(k) for k in ("dirty", "conflict", "archived")
    )
    if index == 0:
        return True
    if index == 1:
        return bool(clean)
    if index == 2:
        return bool(clean and (state.get("workflow") or state.get("candidates")))
    if index == 3:
        return bool(clean and state.get("workflow") and state.get("goal") and state.get("success"))
    return bool(clean and state.get("verified") and not state.get("review_blocked"))


def primary(state, enabled, step):
    if state.get("busy"):
        return "", None, "正在處理，請等待結果。"
    if state.get("conflict"):
        return "比較更新", "compare_project_updates", "你的草稿仍保留，請先核對外部更新。"
    if state.get("archived"):
        return "恢復專案", "restore", "這個專案已封存。"
    if step == 0:
        if not state.get("name"):
            return "", None, "請直接在上方輸入專案名稱。"
        if not state.get("project"):
            return "建立專案並繼續", "create", "目標還沒想完整也可以先保存。"
        if state.get("dirty"):
            return "儲存修改並繼續", "save", ""
        return "下一步：加入來源", "page:1", ""
    if step == 1:
        if state.get("workflow") or state.get("candidates"):
            return "下一步：確認流程", "page:2", ""
        if not state.get("has_sources"):
            return "", None, "選擇上方一種方式，加入工作說明或成功案例。"
        if not state.get("selected"):
            return "", None, "請從來源清單選取要整理的資料。"
        if state.get("folder_needs_more"):
            return (
                "補充成功案例",
                "import_folder",
                "這次沒有找到可採用的流程；請補齊輸入與正確成果。",
            )
        if enabled.get("folder"):
            return "從成功案例找流程", "find_folder_candidates", "先做本機比對，不需要模型。"
        if enabled.get("analysis"):
            return "AI 整理選取來源", "analyze", "送出前會確認資料範圍與預算。"
        return "設定 AI 模型", "configure_model", "也可用上方「手動建立第一個流程」。"
    if step == 2:
        if enabled.get("candidate"):
            return "採用選取的候選流程", "accept_candidate", "採用後仍需試做。"
        if state.get("candidates"):
            if not state.get("goal") or not state.get("success"):
                return "補充工作目標", "page:0", "建立可執行流程前，請補上目標與成功條件。"
            return "", None, "請選擇要採用的候選。"
        if access(state, 3):
            return "下一步：試做", "page:3", "核對流程後，用實際輸入與正確成果試做。"
        return "補充工作目標", "page:0", "請先補齊工作目標與成功條件。"
    if step == 3:
        if state.get("verified"):
            return "下一步：核對與發布", "page:4", "試做通過，接著由你確認成果。"
        if enabled.get("verify"):
            return "建立候選包並試做", "verify", ""
        return "", None, "請先加入試做輸入與正確成果。"
    if state.get("published"):
        return "查看發布版本", "release_history", "流程已發布到本機工作區。"
    if enabled.get("publish"):
        return "確認並發布到本機工作區", "publish", "選取練習發布位置後才安裝流程。"
    return "", None, "請填寫核准者姓名，並勾選已核對成果。"


def refresh(app, context, enabled):
    if not hasattr(app, "wizard_pages"):
        return
    app.wizard_context, app.wizard_enabled = context, enabled
    step = app.wizard_step
    if step and (not context.get("project") or context.get("dirty") or context.get("conflict")):
        show_page(app, 0, internal=True)
        return
    if step == 4 and not access(context, 4):
        show_page(app, 3 if access(context, 3) else 0, internal=True)
        return
    for index, label in enumerate(app.step_labels):
        label.configure(foreground="#2563EB" if index == step else "#64748B")
    app.page_title.set(f"{step + 1} / {len(STEPS)}　{STEPS[step]}")
    label, action, hint = primary(context, enabled, step)
    app.wizard_action = action
    app.guidance_detail.set(hint)
    app.next_button.configure(text=label, state="normal" if action else "disabled")
    if action:
        app.next_button.pack(side="left", padx=(8, 0))
    else:
        app.next_button.pack_forget()
    if step and not app.busy:
        app.back_button.pack(fill="x")
    else:
        app.back_button.pack_forget()
    focused = app.root.focus_get()
    if focused is not None and not focused.winfo_ismapped():
        app.root.after_idle(lambda: focus_page(app))


def advance(app):
    app.refresh_actions()
    action = app.wizard_action
    if not action:
        return
    if action.startswith("page:"):
        show_page(app, int(action.split(":")[1]))
    elif action in {"create", "save"}:
        (app.create_project if action == "create" else app.save_project)()
        show_page(app, 1)
    elif action == "restore":
        app._archive(True)
    else:
        getattr(app, action)()


class OutputPanels:
    """Route existing preview/analysis/report requests to the relevant wizard page."""

    def __init__(self, app):
        self.app = app

    def select(self, index):
        app = self.app
        if index == 0:
            # Selection events can arrive after refresh_project; preview refresh never navigates.
            app.source_tabs.select(0)
        elif index == 2:
            app.repair_panel.pack_forget()
            show_page(app, 3, internal=True)
        elif index == 1 and app.wizard_step == 3:
            app.analysis = app.repair_analysis
            app.repair_panel.pack(fill="both", expand=True)
        else:
            show_page(app, 1, internal=True)
            app.source_tabs.select(index)
            app.analysis = app.source_analysis
