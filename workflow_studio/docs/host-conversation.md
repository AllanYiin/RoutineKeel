# 在對話內設計工作流

在桌面對話說「建立工作流」即可開始。Studio 整理來源及實際回覆，宿主先擬好一份可以直接刪改的初稿。初稿與定稿的所有預填都在對話中展示並請使用者確認。要求安裝時，先逐項展示文字驗收案例，預選先安裝新流程並在實際執行時驗收，使用者可改選合成試做。底座驗證工具和同一份候選套件，收據會顯示實際選擇與實測範圍。

在 Codex 或 Claude Code 開啟 `workflow_studio/`，先讀 `AGENTS.md`，用一句話說明要完成的任務。
宿主負責草擬、提問及呈現修正。RK-NEXT 的 Studio 專案保存來源、回答與版本，供下次對話續編。
不開啟 Tk 或其他獨立視窗，不要求設計期 API 金鑰。

從一句任務就先擬定完整提案。除了目標、輸入、交付和正常步驟，也預想每步的前置與完成條件、常見例外、分支、人工作業、重試、停止點、如何保留既有成果，以及成功與失敗的驗收案例。把有根據的建議值和選擇理由先寫好，使用者可以直接刪去多餘的步驟或調整預設。已說明的要求直接帶入。會改變設計且無法合理預填的問題可以集中釐清。

先用目前需求、本專案的訪談紀錄及使用者指定的來源草擬。只在有具體缺口或使用者要求比對時，查閱明確相關的本機案例。案例及 DEVNOTE 可提供設計線索，採用的內容要列為待校閱的提案。使用者對某一版設計的實際回覆才是該版的確認證據。歷史決策可引用，無法代替新設計的初稿校閱或定稿匯出確認。

使用宿主可用的提問介面，也接受直接文字修正、中途插話、取消與恢復。未回答不能視為同意。
草案包含本次製作重點與方向提醒，並區分每次輸入、設定一次，以及由 Agent 當下判斷的項目與理由。
技能須整份保存並標示對應步驟。MCP 用 `studio_attach_skill` 及工作區內的技能資料夾、ZIP 或其 `SKILL.md` 路徑。CLI 用訪談的 `attach` 並指定 `kind="skill"`。Studio 保存完整資料夾 ZIP、入口文件及每個檔案的雜湊。先按需閱讀，初稿仍展示完整的來源清單。找不到適用技能時，在同一輪初稿校閱詢問是否要補充，使用者也可以確認沿用已有規則。設計時確認能力需求、資料契約和必要限制。執行位置未選定時，定稿的 `tool_locations` 可填 `deferred`，交底座依目標環境接續解決。
一般 Graph 2.3 節點與 Pattern 可共用 `capability_requirement`。在定稿填妥能力、相容版本、具名輸入輸出的語意、允許執行位置、權限、成本及去向限制。目標工作區的底座依上述契約尋找單一工具或可驗證的組合。定稿保留獨立案例和預期成果，宿主可提出替代或 Adapter，資格、隔離試做與安裝依底座收據判定。

藍圖是設計資料，不代表可執行成品或已核准安裝。宿主整理藍圖、合約、依賴、技能及缺口後，
由底座的 `blueprint_completion` 接續補強、試做與安裝；付費、外傳、發布與真人審核沿用各自的授權要求。

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
4. 在對話中展示第二張流程圖 `design_review.final_diagram` 和本版全部 `review_sections`。圖上分別標示底座的「檢核輸入」「檢核輸出」「整理交付結果」及條件式「建立文件索引」、自訂步驟、人工等待、檢核及失敗去向。展示定稿新增和沿用的預填內容後，請使用者另一次確認全數採用並回覆「確認定稿並匯出」。將實際回覆、定稿 ID、`review_hash` 及本版 `shown_sections` 交給 `studio_export_blueprint`。兩張圖與章節內容以白話在對話顯示，保留必要的設定原值。檔案連結供備查。CLI 對應操作為 `prepare_blueprint`、`finalize_workflow_design`、`export_blueprint`。既有工作流演進依序用 `prepare`、`finalize`、`export`，沿用相同校閱契約。

跨對話續編時，用 `studio_get_project` 讀取 `design_progress` 的目前階段及 `next_action`，用 `studio_next_questions` 取得同一階段的提示或待答問題。舊版未匯出的設計如果缺少初稿校閱契約，會指向重新預覽。已保存的使用者答覆可沿用。

交付模式 `deliver_then_refine` 只允許明確標記可容許的機器檢核偏差，交付時列出待修與後續校閱。模式 `stop_on_error` 遇步驟錯誤即停止後續執行。需指定人工檢核時，圖上須有承接該輸出的實際人審節點。拒絕即保存拒絕結果。底座沿用既有的授權、輸入型別與正式交付驗證。

初稿與定稿的 `review_sections.process` 會列出逐步輸入輸出合約。校閱每個具名輸入的來源、型別、格式及必填性，和每個輸出的資料契約與下游用途。定稿同時顯示逐步完成條件。空清單須按步驟用途確認。

透過底座預覽藍圖時，`build_preflight` 列出能力與案例缺口，`build_preflight_guidance.scope` 指向候選試做。依定稿確認流程繼續設計與匯出。使用者要求轉成工作流或安裝時，呼叫 `blueprint_completion` 的 `start` 交給底座接續。

### 藍圖補強與接續

保存底座回傳的 `work_id`。初檢後依 `binding_plan` 繼續。宿主先準備本機元件、技能可重用的程式和相符的模型文字處理。若只有技能規則，先產生可驗證的本機實作或模型指令。查不到合用工具時用官方來源研究，透過 `propose_bindings` 交給底座。依原驗收意圖草擬案例，能合成的素材與預期值先備妥。

呈現 `tool_selection` 時，用一份可勾選的清單展示所有待選步驟、工具、資料轉換、權限、外部目的地、預估上限和已完成的驗證。本機已有的候選優先預選。接受同一則純文字回覆，經 `select_bindings` 提交實際選擇、當前修訂和範圍指紋。依賴安裝、外部服務配置與 Adapter 建置由底座按受信配方執行。新增授權、成本或外傳時，先取得操作者對具體範圍的回覆。候選可沿用已確認案例，變更驗收意圖時先確認新藍圖修訂。

收到 Runtime 的 `REBIND_REQUIRED` 時，保留原 `execution_id`、`session_id` 與 `rebind_work_id`。底座以原藍圖續查候選。原綁定恢復時用 `next` 續跑原 Run。等效候選通過且範圍可沿用時，底座在同一工作建立連回原 Run 的續做 Run。若 `preview_rebind` 標示 `needs_approval`，展示該範圍，將使用者實際同意和指紋轉交 `activate_rebind`。已領取外部操作的結果未知時，先查原操作回執。

工具替代變更了原交付或業務規則時，在 Studio 依原藍圖建立並確認衍生版本。匯出後由原補強工作接收新版本，再驗證受影響的項目。耗時試做時從 `status` 回報實際案例進度，最後依底座收據區分已驗證候選、待升級比較和已安裝。完整工作區的操作與回復例子見 `flow_base/blueprint-install.md`，原始碼儲存庫位於 `flow_base/docs/blueprint-install.md`。

設計階段用完整預填減少使用者輸入，初稿與定稿各保留一次實際確認。使用者只回答部分內容時，保存已答覆項目，繼續呈現尚待校閱的項目。執行中只有藍圖指定的人工作業、關鍵校閱或需授權的動作才等待使用者，其餘步驟在自動契約檢查後繼續。

### 底座通用步驟

RK-NEXT 藍圖 v2 的初稿提供 `draft_design.base_step_catalog`。宿主依工作流需求，以能力 ID、版本 `1`、資料埠及參數引用可選步驟。Studio 定稿時為最外層補上底座執行邊界，將使用到的通用步驟與資料契約保存於 `design_review.base_steps`，連同流程圖綁定 `review_hash`。子流程由其父節點展開。

| 底座邊界步驟 | 時機與輸入 | 輸出 |
| --- | --- | --- |
| **檢核輸入** `base.input.check` | 取得具名輸入與藍圖輸入埠，檢查必填欄位、型別和素材引用。 | 已檢核的 `inputs` 與 `inputs_hash`。 |
| **建立文件索引** `base.document.index` | 輸入有特殊文件時，於開跑前用本機 Documa 解析。 | `document_indexes`，以來源素材引用為鍵，保存 Documa IR、ContextIR、解析器及圖索引摘要。 |
| **檢核輸出** `base.output.check` | 所有具名輸出可取得後，核對資料埠及檔案，執行品質契約。 | `assessment_id`、品質結論及交付資格。未具交付資格時保留可用預覽與證據。 |
| **整理交付結果** `base.delivery.prepare` | 取得通過檢核的具名成果與評估 ID。 | 版本化持久交付、可點選的成果連結、機器可讀索引、ZIP 與交付清單。 |

可選步驟可置於任何符合資料依賴的工作流位置。Studio 設定各步驟的門檻、來源、失敗分支和人工等待點。

| 可選底座步驟 | 輸入 | 輸出 |
| --- | --- | --- |
| **擬定勾稽計畫** `base.reconciliation.plan` | `parameters.rules`，指定兩側 JSON Pointer、比較方式及容差。 | `plan` 物件。相同版本與規則只建置一次，之後重用有指紋的本機快取。 |
| **執行勾稽** `base.reconciliation.run` | `plan` 物件與本次 `left`、`right` JSON。 | `matched` 布林值與 `report` 物件。支援等值、計數、十進位加總與逐鍵比對。可區分差異與輸入錯誤。 |
| **查詢文件索引** `base.document.search` | `indexes` 物件、`query` 字串，可選 `route`。 | `matches` 陣列，含文件來源、區塊 ID、摘錄與可用的圖路徑。 |
| **載入技能** `base.skill.load` | `task` 字串，以及可選的 `skill_names`、`max_tokens`。 | `bundle` 物件，含 Documa 載入狀態、選用技能、圖依賴、來源及 token 預算。 |
| **模型審查** `base.model.judge` | `candidate` JSON 與 `rubric` 物件。 | `verdict` 物件，含 `passed`、`rationale`、`findings`。 |
| **批評者** `base.model.critic` | `candidate` JSON 與 `rubric` 物件。 | `critique` 物件，含 `passed`、`rationale`、`findings`。未通過時須具體指出待修項。 |
| **修訂者** `base.model.reviser` | 同一份 `candidate` JSON 與批評者的 `critique`。 | `candidate` JSON。批評通過時直接傳遞原成果，否則呼叫已綁定模型修訂。 |

數據與報表工作流若有可互證的資料或彙總，設計時加入勾稽計畫與執行勾稽，確認比較基準後再定稿。規則的 `operation` 可選 `equals`、`count`、`sum` 或 `keyed`。`left` 與 `right` 是指向兩側資料的 JSON Pointer，空字串指向整份值。`sum` 可用 `left_field` 和 `right_field` 指向各列數值。`keyed` 用 `left_key`、`right_key` 指定各列唯一鍵，並可比較指定欄位。Studio 可用 `matched` 設定關鍵機器檢核或分支。模型步驟使用宿主清冊中的同名能力、串流與用量回執，工作流可依 `verdict.passed` 分支。若需多輪回修，使用既有 `repeat_until` 宣告上限及失敗去向。

### Documa 文件與技能

特殊文件包含 PDF、Word、Excel、PowerPoint、HTML、郵件與 Notebook。Runtime 使用本機 `documa`，也可由宿主透過 `ROUTINEKEEL_DOCUMA` 指定絕對執行檔路徑。必要的解析器缺失、格式不支援或 Documa 報錯時會回報原階段失敗。新定稿藍圖若要讓下游使用索引，在最外層 `inputs` 宣告 `document_indexes` 為 `object`，以 `input://document_indexes` 綁定查詢節點的 `indexes`。此值由底座產生。非文件執行得到空物件。

Studio 匯入特殊文件時保存 Documa 預覽及索引指紋。使用 `studio_search_document` 查詢已匯入的 `source_id`、`relative` 與指定 `version`，也可選擇 `graph-first` 路徑。CLI 對應 `interview search_document`。

宿主先在自己管理的 Documa 技能目錄配置受信來源與索引，再把 `ROUTINEKEEL_DOCUMA_SKILL_STORE` 指向其 store。Studio 用 `studio_load_skills` 或 `interview load_skills`，執行圖用 `base.skill.load`。三者使用同一個 Documa loader。遇到 `needs_narrowing` 時依 Documa 的候選與原因處理。藍圖內附的 `skills/` 保存確認過的來源版本與檔案。重新建置帶技能的 v1 藍圖時，底座以暫存的 Documa catalog 依其技能依賴載入。技能文字本身無法授權外部動作。

Studio CLI 支援 `interview next_batch|reply_batch` 及既有單題操作，參數透過 `--payload` 傳入。底座 CLI 使用相同操作名。另有 `interview stream` 可逐行接收含 text 的 JSON 文字增量，立即輸出 delta，正常收束時才輸出 completed。此入口不會保存回答。

本機 MCP stdio 連線首次執行可重用的 Studio 操作時，啟動其隔離環境的 Python。後續操作可共用該子程序，連線結束時回收。每次操作仍從 Studio 資料庫載入目前狀態。若請求逾時或連線中斷，先重新查詢已保存的版本，再決定是否以同一請求識別碼重送。獨立 CLI、舊版 Studio，以及需單獨傳遞 Runtime 證據的 Pattern 改善操作，沿用逐次啟動。

完整發行包以 Studio 啟動器的 `--prepare-only` 建立隔離環境。若缺少 `workflow_studio/runtime/`，先核對完整發行包再開始訪談。開發工作樹可執行 `uv sync --locked --project workflow_studio --group dev`。另裝的 Studio 可用本機設定 `ROUTINEKEEL_STUDIO_PYTHON` 指向已信任的絕對 Python 路徑。工作區路徑僅用於資料。
