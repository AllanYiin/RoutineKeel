"""Local next-step guidance. It never grants permission or invokes a service."""

from typing import NamedTuple


class NextStep(NamedTuple):
    title: str
    detail: str
    label: str
    action: str | None


def next_step(state):
    if state.get("busy"):
        return NextStep(
            "正在處理", "請等待目前操作結束；分析中可使用「停止分析」。", "處理中", None
        )
    if not state.get("project"):
        for field, label in (("name", "專案名稱"), ("goal", "工作目標"), ("success", "成功條件")):
            if not state.get(field):
                return NextStep(
                    "1 · 說清楚要完成的工作",
                    f"先填寫「{label}」，再建立專案。",
                    f"填寫{label}",
                    "focus_" + field,
                )
        return NextStep(
            "1 · 建立專案",
            "你的目標已填好。先保存專案，才能加入說明與案例。",
            "建立專案，繼續下一步",
            "create_project",
        )
    if state.get("conflict"):
        return NextStep(
            "先核對外部更新",
            "其他操作更新了專案；你的文字仍保留，請比較後再儲存。",
            "比較更新",
            "compare_project_updates",
        )
    if state.get("archived"):
        return NextStep(
            "此專案已封存", "仍可查看歷史；恢復後才能繼續編輯。", "恢復專案", "restore_project"
        )
    if state.get("dirty"):
        for field, label in (("name", "專案名稱"), ("goal", "工作目標"), ("success", "成功條件")):
            if not state.get(field):
                return NextStep(
                    "先完成修改",
                    f"請補上「{label}」，再儲存目前目標。",
                    f"填寫{label}",
                    "focus_" + field,
                )
        return NextStep(
            "先儲存修改",
            "目標有尚未儲存的內容。儲存後，來源與流程操作就能繼續。",
            "儲存目標並繼續",
            "save_project",
        )
    if not state.get("has_sources"):
        return NextStep(
            "2 · 加入工作說明或成功案例",
            "可先貼上文字，補充輸入、成果和規則；也可以從左側匯入檔案或成功案例資料夾。",
            "加入文字說明",
            "paste_source",
        )
    if state.get("candidates"):
        return NextStep(
            "3 · 檢查候選流程",
            "先從流程上方的清單選擇候選，再採用。採用後仍需要試做。",
            "採用選取的候選" if state.get("candidate_selected") else "選擇候選流程",
            "accept_candidate" if state.get("candidate_selected") else "focus_candidate",
        )
    if not state.get("workflow"):
        if not state.get("selected"):
            return NextStep(
                "3 · 選擇要整理的來源",
                "點選左側剛匯入的來源；Ctrl／Command 可多選。選取後會顯示實際預覽。",
                "前往來源清單",
                "focus_sources",
            )
        if state.get("single_folder"):
            if state.get("folder_needs_more"):
                return NextStep(
                    "案例資料還不完整",
                    "這份案例沒有找到可採用的流程。請補上原始輸入與正確成果後，重新匯入；不要反覆按相同的比對。",
                    "匯入補完整的案例",
                    "import_folder",
                )
            return NextStep(
                "3 · 從成功案例找出流程",
                "資料夾應包含輸入與正確成果。先做本機比對，不需要模型金鑰。",
                "從成功案例找流程",
                "find_folder_candidates",
            )
        if not state.get("provider"):
            if state.get("provider_configured"):
                return NextStep(
                    "3 · 補入本次模型金鑰",
                    "模型設定已保留，但金鑰只留在本次工作台；請在模型設定補入金鑰後再分析。",
                    "開啟模型設定",
                    "configure_model",
                )
            return NextStep(
                "3 · 選擇製作方式",
                "要讓 AI 整理文字或圖片，需先設定模型；也可用下方「手動建立第一個流程」選擇基本動作。",
                "設定 AI 模型",
                "configure_model",
            )
        return NextStep(
            "3 · 整理工作步驟",
            "來源已選取。送出前會再顯示資料範圍、模型與預算，確認後才呼叫 AI。",
            "AI 整理選取來源",
            "analyze",
        )
    if state.get("review_blocked"):
        return NextStep(
            "先處理建置審核",
            "目前建置已退回或封存，請先查看原因；不能直接發布。",
            "查看建置與審核",
            "manage_builds",
        )
    if not state.get("cases"):
        return NextStep(
            "4 · 提供正確成果樣本",
            "選一份實際輸入及你認為正確的成果，讓試做有明確的比較依據。",
            "選取輸入與正確成果",
            "add_case",
        )
    if state.get("report_failed"):
        if state.get("repairable"):
            return NextStep(
                "4 · 根據差異修復",
                "保留正確成果，讓 AI 提出修改。送出前會再確認資料與預算，最多修復兩輪。",
                "查看修復範圍",
                "repair",
            )
        return NextStep(
            "4 · 試做尚未通過",
            "先看右側報告與差異，調整來源或流程後再試做；保留原本的正確成果。",
            "查看試做報告",
            "show_results",
        )
    if not state.get("verified"):
        return NextStep(
            "4 · 試做並核對成果",
            "流程與案例已備齊。現在可以在隔離工作區試做，不會改動正式版本。",
            "建立候選包並試做",
            "verify",
        )
    if state.get("published"):
        return NextStep(
            "5 · 流程已發布",
            "可交由底座執行；修改時另存版本並重新試做，舊版本仍保留。",
            "查看發布版本",
            "release_history",
        )
    if not state.get("reviewed"):
        return NextStep(
            "5 · 由你核對試做成果",
            "試做通過不等於已核准。請在「試做與發布」檢查報告，再勾選成果確認。",
            "前往成果確認",
            "focus_review",
        )
    if not state.get("actor"):
        return NextStep(
            "5 · 記錄核准者",
            "請填寫核准者姓名，接著選擇本機發布位置。",
            "填寫核准者姓名",
            "focus_actor",
        )
    return NextStep(
        "5 · 發布已核對的版本",
        "選擇本機工作區安裝這個版本；不會發布到網路或自動對外寄送。",
        "選擇發布位置",
        "publish",
    )


def blocked_reason(rule, state):
    if state.get("busy"):
        return "目前操作還在處理，請等待完成；分析可使用「停止分析」。"
    if rule == "new":
        if state.get("project"):
            return "已有專案時，請先按「＋ 新專案」。"
        return "請先填完整的專案名稱、工作目標與成功條件，再建立專案。"
    if not state.get("project"):
        return "先填寫目標並按「建立專案」。建立後就能加入來源。"
    if rule == "archived":
        return "目前專案未封存，不需要恢復。"
    if state.get("conflict"):
        return "有外部更新尚未核對；請先按「比較更新」。你的草稿仍保留。"
    if state.get("archived"):
        return "此專案已封存；先恢復專案才能繼續修改或執行。"
    if state.get("dirty"):
        return "目標有尚未儲存的修改；請先按「儲存目標」。"
    if rule in {"source", "single_source", "analysis", "folder"}:
        if not state.get("selected"):
            return "請先點選左側來源；只有匯入、尚未選取時，仍不能執行這項操作。"
        if rule == "single_source" and state.get("selected_count", 0) != 1:
            return "這項操作一次只能處理一個來源；請只選一個來源或資料夾。"
        if rule == "analysis" and not state.get("provider"):
            if state.get("provider_configured"):
                return (
                    "模型設定已保存，但本次還沒有 API 金鑰；請開「模型設定」補入，未傳送任何資料。"
                )
            return "AI 分析需要先完成右上角「模型設定」。成功案例資料夾也可使用本機比對。"
        if rule == "folder":
            return "請只選一個成功案例資料夾或 ZIP；文字說明可使用 AI 整理或手動起案。"
    if rule == "candidate":
        return "先整理來源或比對成功案例，再從出現的候選清單選擇一項。"
    if rule == "initialize":
        return "已有流程請用「新增步驟／連線／交付物」。尚無流程時，先加入至少一個來源。"
    if rule in {"workflow", "node", "save_handler", "verify"} and not state.get("workflow"):
        return "先採用候選流程，或用「手動建立第一個流程」建立步驟。"
    if rule == "node":
        return "請先點選中間流程圖的一個步驟，再調整或刪除它。"
    if rule in {"cases", "case_history", "verify"}:
        return "先在「試做與發布」選取實際輸入與正確成果樣本。"
    if rule == "save_handler":
        return "只有已通過試做、且選取的是自訂資料處理步驟，才能保存為可重用工具。"
    if rule == "repair":
        return "需要已設定模型，以及目前版本通過安全檢查但業務比對失敗的報告，才能提出修復。"
    if rule == "publish":
        if state.get("review_blocked"):
            return "此建置已退回或封存；請先在建置與審核查看原因。"
        if not state.get("verified"):
            return "先完成目前版本的試做，所有案例通過後才能核准發布。"
        if not state.get("reviewed"):
            return "請先核對試做成果，並勾選「已核對試做成果與工作目標」。"
        return "請填寫核准者姓名，再選擇發布位置。"
    return "目前尚不符合操作條件，請依上方「下一步」繼續。"
