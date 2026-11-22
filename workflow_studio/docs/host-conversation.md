# 在對話內設計工作流

在桌面對話說「建立工作流」即可開始。Studio 將回答與來源整理成設計資料。確認摘要後，底座建置候選工作流，將它載入臨時工作區執行驗收案例。只有通過的同一份套件能安裝到正式工作區。以實際回覆確認重要決策即可，使用者全程留在對話裡。

在 Codex 或 Claude Code 開啟 `workflow_studio/`，先讀 `AGENTS.md`，用一句話說明要完成的任務。
宿主負責草擬、提問及呈現修正。RK-NEXT 的 Studio 專案保存來源、回答與版本，供下次對話續編。
不開啟 Tk 或其他獨立視窗，不要求設計期 API 金鑰。

先從一句任務擬定目標、輸入、交付、處理骨架及驗收，將彼此獨立的必要決策集中在同一輪。使用者已說明的要求直接帶入草案。只有答案確實會改變後續選項時才追問，最後用一份摘要確認設計。

使用宿主可用的提問介面，也接受直接文字修正、中途插話、取消與恢復。未回答不能視為同意。
草案包含本次製作重點與方向提醒，並區分每次輸入、設定一次，以及由 Agent 當下判斷的項目與理由。
技能須整份保存並標示對應步驟。先檢查候選能力是否有本機可用的工具、版本、輸入輸出、節點參數及權限契約。發現缺口時，在確認設計前明列受阻節點。安裝時再重新核對。

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

宿主透過 `studio_create_project`、`studio_next_questions` 與 `studio_get_project` 進行訪談。可指定本輪要問的領域，使用者可在同一則訊息回答多項。模型產生提問、建議或附件摘要時，在對話中隨生成逐段顯示。若已有 Studio 專案，使用 `studio_start_interview` 並提供目前專案版本。

宿主取得實際使用者訊息後，以 `studio_record_replies` 提交整則原文、同一個 `expected_revision`、`request_id`，以及已回答的 `replies`。每個回答附 `domain`、`question_id`，可用 `answer_text` 指定屬於該題的逐字片段。明確採用已顯示的草案時使用 `accept_proposal`，拒絕與稍後處理分別用 `decline`、`defer` 保存。每批回答原子保存於同一版。單題介面仍可使用，模型建議也可集中放進藍圖摘要。

透過底座預覽藍圖時，`build_preflight` 會列出當前宿主的能力缺口及不可執行的案例。文字描述可保留在設計包，建置驗收案例須提供 `inputs`、`expected` 及必要的素材或校閱回覆。預檢通過後，仍須在臨時工作區試做。

工作流設計只在影響結果的重要決定提問。執行中只有藍圖指定的人工作業、關鍵校閱或需授權的動作才等待使用者，其餘步驟在自動契約檢查後繼續。

Studio CLI 支援 `interview next_batch|reply_batch` 及既有單題操作，參數透過 `--payload` 傳入。底座 CLI 使用相同操作名。另有 `interview stream` 可逐行接收含 text 的 JSON 文字增量，立即輸出 delta，正常收束時才輸出 completed。此入口不會保存回答。

完整發行包以 Studio 啟動器的 `--prepare-only` 建立隔離環境。若缺少 `workflow_studio/runtime/`，先核對完整發行包再開始訪談。開發工作樹可執行 `uv sync --locked --project workflow_studio --group dev`。另裝的 Studio 可用本機設定 `ROUTINEKEEL_STUDIO_PYTHON` 指向已信任的絕對 Python 路徑。工作區路徑僅用於資料。
