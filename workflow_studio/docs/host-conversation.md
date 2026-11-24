# 在對話內設計工作流

在桌面對話說「建立工作流」即可開始。Studio 整理來源及實際回覆，宿主先擬好一份可以直接刪改的初稿。初稿與定稿的所有預填都在對話中展示並請使用者確認。定稿確認後，底座建置候選工作流，將它載入臨時工作區執行驗收案例。只有通過的同一份套件能安裝到正式工作區。

在 Codex 或 Claude Code 開啟 `workflow_studio/`，先讀 `AGENTS.md`，用一句話說明要完成的任務。
宿主負責草擬、提問及呈現修正。RK-NEXT 的 Studio 專案保存來源、回答與版本，供下次對話續編。
不開啟 Tk 或其他獨立視窗，不要求設計期 API 金鑰。

從一句任務就先擬定完整提案。除了目標、輸入、交付和正常步驟，也預想每步的前置與完成條件、常見例外、分支、人工作業、重試、停止點、如何保留既有成果，以及成功與失敗的驗收案例。把有根據的建議值和選擇理由先寫好，使用者可以直接刪去多餘的步驟或調整預設。已說明的要求直接帶入。會改變設計且無法合理預填的問題可以集中釐清。

先用目前需求、本專案的訪談紀錄及使用者指定的來源草擬。只在有具體缺口或使用者要求比對時，查閱明確相關的本機案例。案例及 DEVNOTE 可提供設計線索，採用的內容要列為待校閱的提案。使用者對某一版設計的實際回覆才是該版的確認證據。歷史決策可引用，無法代替新設計的初稿校閱或定稿匯出確認。

使用宿主可用的提問介面，也接受直接文字修正、中途插話、取消與恢復。未回答不能視為同意。
草案包含本次製作重點與方向提醒，並區分每次輸入、設定一次，以及由 Agent 當下判斷的項目與理由。
技能須整份保存並標示對應步驟。設計時確認能力需求、資料契約和必要限制。執行位置尚未選定時，定稿的 `tool_locations` 對應步驟可填 `deferred`。實際工具綁定由底座在目標工作區建置時解析，正式安裝前重驗。
一般 Graph 節點由底座按藍圖的能力識別與版本查找實作。由 Pattern 提供可攜能力需求時，底座可依需求選擇經資格驗證的 Adapter。

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

宿主取得實際使用者訊息後，以 `studio_record_replies` 提交整則原文、同一個 `expected_revision`、`request_id`，以及已回答的 `replies`。每個回答附 `domain`、`question_id`，可用 `answer_text` 指定屬於該題的逐字片段。使用者明確回覆「採用提案」時使用 `accept_proposal`，拒絕與稍後處理分別用 `decline`、`defer` 保存。每批回答原子保存於同一版。單題介面仍可使用，模型建議也可集中放進藍圖摘要。

### 初稿、參數研究與定稿確認

1. 用 `studio_preview_blueprint` 取得初稿。在對話中先展示第一張 `draft_design.diagram`，圖上只顯示工作流步驟與分支。依 `review_sections` 四組清單在對話中逐項展示目標、輸入與交付、步驟與例外、技能工具與變數、業務規則和驗收的實際提案值。使用者可逐組校閱、一起確認，或直接指出要刪改、暫緩的內容。以 `studio_record_replies` 記錄實際答覆，重新預覽變更的初稿。暫緩或拒絕須有新的使用者答覆才能解除。
2. 宿主依 `draft_design.research_requests` 使用網頁搜尋，帶入工具名、版本及參數名。用 `studio_research_parameter_source` 或 CLI `research_parameter_source` 提交公開第一手 HTTPS 頁面 `url` 與含採用值的原文 `excerpt`。Studio 會擷取實際文字並回傳 `accessed_on` 與 `source_sha256`。研究紀錄也需提供查詢、標題、適用條件、理由及採用值。入口接受支援網域的 HTML、純文字及 Markdown 頁面。證據不足時，建議值留在初稿。
3. 完成初稿所有章節的展示及討論後，詢問使用者是否同意全部預填並回覆「初稿可進入定稿」。用 `studio_finalize_workflow_design` 提交相同初稿 ID、預期版次、具名請求、`review`、實際回覆 `draft_reply` 及 `shown_sections`。`shown_sections` 為已展示章節 ID 到 `review_sections[id].sha256` 的對照，由宿主轉交。在 `step_decisions` 逐步預填 `purpose`、`completion`、`critical`、`rationale`。在 `policy` 為所有關鍵步驟設定檢核，並說明模式及範圍。定稿另填 `skills`、`tool_locations`、`glossary` 及 `variables`。模型或門檻選值改變時，先更新初稿。可調變數的已確認值跟隨藍圖版本。
4. 在對話中展示第二張流程圖 `design_review.final_diagram` 和本版全部 `review_sections`。圖上分別標示底座的「檢核輸入」「檢核與交付輸出」、自訂步驟、人工等待、檢核及失敗去向。展示定稿新增和沿用的預填內容後，請使用者另一次確認全數採用並回覆「確認定稿並匯出」。將實際回覆、定稿 ID、`review_hash` 及本版 `shown_sections` 交給 `studio_export_blueprint`。兩張圖與章節內容以白話在對話顯示，保留必要的設定原值。檔案連結供備查。CLI 對應操作為 `prepare_blueprint`、`finalize_workflow_design`、`export_blueprint`。既有工作流演進依序用 `prepare`、`finalize`、`export`，沿用相同校閱契約。

跨對話續編時，用 `studio_get_project` 讀取 `design_progress` 的目前階段及 `next_action`，用 `studio_next_questions` 取得同一階段的提示或待答問題。舊版未匯出的設計如果缺少初稿校閱契約，會指向重新預覽。已保存的使用者答覆可沿用。

交付模式 `deliver_then_refine` 只允許明確標記可容許的機器檢核偏差，交付時列出待修與後續校閱。模式 `stop_on_error` 遇步驟錯誤即停止後續執行。需指定人工檢核時，圖上須有承接該輸出的實際人審節點。拒絕即保存拒絕結果。底座沿用既有的授權、輸入型別與正式交付驗證。

初稿與定稿的 `review_sections.process` 會列出逐步輸入輸出合約。校閱每個具名輸入的來源、型別、格式及必填性，和每個輸出的資料契約與下游用途。定稿同時顯示逐步完成條件。空清單須按步驟用途確認。

透過底座預覽藍圖時，`build_preflight` 會列出目前工作區的能力及可執行案例缺口。正常接續設計校閱與匯出，在目標工作區解析能力和補齊案例後才建置試做。文字描述可保留在設計包，建置驗收案例須提供 `inputs`、`expected` 及必要的素材或校閱回覆。

設計階段用完整預填減少使用者輸入，初稿與定稿各保留一次實際確認。使用者只回答部分內容時，保存已答覆項目，繼續呈現尚待校閱的項目。執行中只有藍圖指定的人工作業、關鍵校閱或需授權的動作才等待使用者，其餘步驟在自動契約檢查後繼續。

Studio CLI 支援 `interview next_batch|reply_batch` 及既有單題操作，參數透過 `--payload` 傳入。底座 CLI 使用相同操作名。另有 `interview stream` 可逐行接收含 text 的 JSON 文字增量，立即輸出 delta，正常收束時才輸出 completed。此入口不會保存回答。

完整發行包以 Studio 啟動器的 `--prepare-only` 安裝到根層底座 `.venv`。整合安裝請使用同一次發行的底座與 Studio ZIP。單獨安裝的 Studio 使用隨附底座建立的 `.venv`。開發工作樹可執行 `uv sync --locked --project workflow_studio --group dev`。另裝的 Studio 可用本機設定 `ROUTINEKEEL_STUDIO_PYTHON` 指向已信任的絕對 Python 路徑。`--workspace` 選擇的是資料目錄。
