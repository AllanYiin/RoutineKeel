# 在對話內設計工作流

在桌面對話說「建立工作流」即可開始。Studio 將回答與來源整理成設計資料。確認摘要後，底座建置候選工作流，將它載入臨時工作區執行驗收案例。只有通過的同一份套件能安裝到正式工作區。以實際回覆確認重要決策即可，使用者全程留在對話裡。

在 Codex 或 Claude Code 開啟 `workflow_studio/`，先讀 `AGENTS.md`，用一句話說明要完成的任務。
宿主負責草擬、提問及呈現修正。RK-NEXT 的 Studio 專案保存來源、回答與版本，供下次對話續編。
不開啟 Tk 或其他獨立視窗，不要求設計期 API 金鑰。

引導順序：一句任務 → 釐清 → 步驟骨架 → 輸入輸出 → 工具清冊 → 檢核清冊 → 審核方式 → 任務設定檔 → 藍圖包。

每層先由宿主提出有依據的草案，再請作者修正與確認。問題只涵蓋當前必要決策；工具回傳 JSON 不代表使用者已回答。
使用宿主可用的提問介面，也接受直接文字修正、中途插話、取消與恢復。未回答不能視為同意。
第 3 層須包含本次製作重點與方向提醒；第 7 層區分每次輸入、設定一次、Agent 當下判斷並記錄理由。
技能須整份保存並標示對應步驟；未知工具能力保留為缺口，安裝時才綁定當地可用工具。

藍圖是設計資料，不代表可執行成品或已核准安裝。宿主整理藍圖、合約、依賴、技能及缺口後，
依底座的 `install-workflow` 介面另行試做與安裝；付費、外傳、發布與真人審核沿用各自的授權要求。

命令檢查：

```powershell
uv run --project workflow_studio python -m workflow_studio guide
uv run --project workflow_studio python -m workflow_studio --workspace <RoutineKeel根目錄> projects
uv run --project workflow_studio python -m workflow_studio --workspace <RoutineKeel根目錄> status
```

安裝包可使用 `studio.bat guide` 或 `sh studio.sh guide`。`guide` 只輸出引導資料，不建立資料庫、不呼叫模型、不開視窗。
`guide` 本身只顯示操作順序。RK-NEXT 開發版可由 `StudioService.interviews` 建立訪談、取得目前問題所需的覆蓋資料、保存答覆與暫停或恢復。以原 project_id 開啟即可跨程序續編。回答寫入綁定版本與 request_id；宿主草案保留推論來源，真人回覆須由互動入口綁定訪談、問題與版本。
需要操作既有專案核心時，使用 `workflow_studio.conversation.session.StudioSession` 或 `StudioService` 的公開方法。
核心寫入維持版本檢查；來源內容不得變成執行權限。舊 Tk 草稿不自動轉換為已確認的訪談答案。

## RK-NEXT 訪談

宿主透過 `studio_create_project`、`studio_next_question`、`studio_submit_answer` 與 `studio_get_project` 進行訪談。模型產生提問、建議或附件摘要時，在對話中隨生成逐段顯示。顯示未確認草稿、來源、修改方法及待決項目；略過或拒絕的問題保留為缺口。若已有 Studio 專案，使用 `studio_start_interview` 並提供目前專案版本。

使用者在目前桌面對話直接回答即可。宿主先顯示問題及建議，等下一則實際使用者訊息，再用 `studio_record_reply` 送回原文、question_id、expected_revision 與本次 request_id。若使用者明確採用已顯示的草稿，使用 accept_proposal；拒絕與稍後處理分別以 decline、defer 保存。模型產生的建議走 studio_submit_answer。此設計以桌面宿主作為對話來源，保留訊息參照及原文供追溯。

工作流設計只在影響結果的重要決定提問。執行中只有藍圖指定的人工作業、關鍵校閱或需授權的動作才等待使用者，其餘步驟在自動契約檢查後繼續。

Studio CLI 支援 `interview create|next|reply|answer|revise|skip|reject|pause|resume|get|history|preview`，參數透過 `--payload` 傳入；底座 CLI 使用相同操作名。另有 `interview stream` 可逐行接收含 text 的 JSON 文字增量，立即輸出 delta，正常收束時才輸出 completed。此入口不會保存回答。

完整發行包以 Studio 啟動器的 `--prepare-only` 建立隔離環境。開發工作樹可執行 `uv sync --locked --project workflow_studio --group dev`。另裝的 Studio 可用本機設定 `ROUTINEKEEL_STUDIO_PYTHON` 指向已信任的絕對 Python 路徑。工作區路徑僅用於資料。
