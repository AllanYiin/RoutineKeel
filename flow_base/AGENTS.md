# flow_base 開發規則

## 核心規則

- 用繁體中文呈現學員介面；首次啟動不要求先安裝 Python 或 uv。
- 保留使用者指定的六類根資料夾；底座與任務分包。
- 需求以 specs/requirements.md 為準，工作清單使用 todo.md。

## 共用工具情境

- 要求將藍圖轉成可試做或可安裝的工作流時，使用 `blueprint-completion` 或 MCP `blueprint_completion`（安裝用 `intent=install`）。初檢是補強入口。保存原 `work_id`，持續準備相符的工具及候選驗證。安裝核對同一套件的必要驗證與驗收策略收據。
- 先處理 `binding_plan.automatic_work`，盤點本機環境及已配置模型，讀該步驟引用的技能及腳本。宿主負責接線、編寫具名輸出映射和案例。需要外部資料或元件時先核對原廠來源。清冊斷線或尚未有 Adapter 時，仍先產生可比較的本機／技能／研究方案，走 `propose_bindings` 保存研究。
- 用 `binding_plan.tool_selection` 一次展示所有真正待選的工具及建議預設，說明可用性、資料去向與實際取捨。支援勾選或同一則文字答覆，以 `select_bindings` 交回。底座與宿主依已選方案重用或編寫 Adapter、準備具名資料映射與案例、執行資格驗證，並沿原 `work_id` 續作。Adapter、清冊、接線及測試素材由底座與宿主處理。
- 宿主有已配置的工具來源時，由程式取得實際工具清單與既有授權。本機元件、宿主連線與 Adapter 資格分別查核。已通過的實測用於接線及案例，資格由底座呼叫實作取得。清冊缺漏或未綁定時，先完成能獨立驗證的本機步驟。
- 必要元件缺少時，先檢查已配置的位置及本機等效方案，再上網查一手取得或安裝方式。展示版本、來源、範圍及回復方式，取得同意後由宿主準備、驗證並沿原工作續作。其他可做項目繼續進行。只有相容方案、必要授權或實測條件確實無法取得時才停在該缺口。
- 以 `case_review` 先在對話逐項展示文字案例、預期和目前狀態。預選 `observe_runtime`，先安裝新流程並於實際 Run 觀察。接受使用者改選合成試做。以 `choose_case_strategy` 保存實際回覆與案例範圍。合成方案由宿主準備素材和獨立預期；先安裝方案仍須通過工具資格、套件驗證及隔離載入，並在收據和實際 Run 標示待驗收。
- Graph 2.3 的工具鏈規劃、隔離準備與資格驗證走底座通用契約。驗收使用已確認的案例；清冊及其實際授權由宿主管理。
- Runtime 回 `REBIND_REQUIRED` 時保存原工作 ID、Run 及 `rebind_work_id`。以原 `session_id` 查詢、預覽和續做。續做允許沿用已驗證的成果。已領取且結果不明的外部操作先查回執。操作入口見 `docs/blueprint-install.md`。

- 使用情境為 scene_b_shared_tool；錯誤需可複製、可追查、可重試。
- 輸入、輸出、快取與版本的既有契約保持相容。
- flow_id 與 job_id 可延續，每次執行建立新 run_id。

## 技術補充

- Python CLI 和 MCP stdio 共用 WorkflowService。宿主呈現選項，可用勾選或純文字回覆。兩種入口的工作狀態、版次及操作契約一致。
- scripts/project_launcher.py 生成 run_app.bat、run_app.command、run_app.sh。
- 安裝機制集中於 tools/start.ps1.in、tools/start.sh.in（相對儲存庫根目錄）。
- 禁止將多個 Get-Command 結果當成一個執行檔；先實際驗證選定 uv。
- 不把 .venv、模型、憑證、日誌或測試用 PowerShell 打包。
- 使用相對工作區位置；主流程不以自然語言組合 shell 命令。
- MCP 提交正式工作時使用 SQLite 佇列；`queue-worker` 由使用者另行啟動。流程包管理與具名宿主待辦走 WorkflowService；安裝試做使用拋棄式驗證工作區。不安裝常駐 HTTP／OS 服務。

## 專案特例

- APSM 原矩陣未含 CLI，使用 project.config.json 的 flow_base_cli_v1 明示擴充。
- 開發規格、scripts 與檢查工具封裝在 source.zip，學員不需操作。
- runtime metadata 存 share_states/launcher，安裝 log 存 share_logs/launcher；不建立虛構 port。
- 非互動命令的 stdout/stderr 必須保留到執行結束；進入互動聊天前停止 transcript。錯誤另寫結構化紀錄，不序列化完整輸入或 frame locals，遮罩已知環境金鑰。
- 介面唯一主任務為準備並進入流程入口；狀態與錯誤依 specs/requirements.md 的揭露規則呈現。
- 從開發工作區執行 python tools/release.py --project flow_base。
- 透過 `uv run --locked --project flow_base python tools/release.py --project flow_base` 檢查 CLI 擴充契約。打包會在拋棄式副本產生 run_app 啟動器，再執行 APSM strict。
- 測試執行 uv run --project flow_base --group dev pytest tests workflows -q。
- PowerShell 測試需設定 FLOW_BASE_TEST_PWSH，未實測 Windows/macOS 雙擊時明示限制。

## 解耦邊界

- 底座只實作通用契約與執行服務；禁止 import 任務包或硬編碼模型、字幕、QA、聽審政策。
- 領域設定、依賴、資源、特殊操作由流程包維護；底座以 manifest 與 Adapter 派送。
- 移除任何流程後，底座與其他流程仍可運作；新增領域行為不要求底座同步修改。
- 特殊文件解析、索引與查詢一律經 Documa。受管理技能以 Documa graph 技能載入器取得，宿主提供受信目錄與能力綁定。

## 任務產物

- 新產物預設放目前流程 outputs/<工作>/，不使用 share_states/outputs 或流程根目錄。
- docs、artifacts、scripts 分類由底座契約提供；scripts 是任務交付腳本，非框架原始碼。按需建立，不自動執行。
- report.json、deliverables.json、docs/run-summary.md 由底座維護。封裝排除 outputs，更新保留既有成果。
