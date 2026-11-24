# flow_base 開發規則

## 核心規則

- 用繁體中文呈現學員介面；首次啟動不要求先安裝 Python 或 uv。
- 保留使用者指定的六類根資料夾；底座與任務分包。
- 需求以 specs/requirements.md 為準，工作清單使用 todo.md。

## 共用工具情境

- 要求將藍圖轉成可試做或可安裝的工作流時，使用 `blueprint-completion` 或 MCP `blueprint_completion`。依實際缺口與修訂接續補強，正式安裝須持有同一候選的必要驗證與試做收據。

- 使用情境為 scene_b_shared_tool；錯誤需可複製、可追查、可重試。
- 輸入、輸出、快取與版本的既有契約保持相容。
- flow_id 與 job_id 可延續，每次執行建立新 run_id。

## 技術補充

- Python CLI 為共用入口；不新增瀏覽器或任務專屬 UI。
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

## 任務產物

- 新產物預設放目前流程 outputs/<工作>/，不使用 share_states/outputs 或流程根目錄。
- docs、artifacts、scripts 分類由底座契約提供；scripts 是任務交付腳本，非框架原始碼。按需建立，不自動執行。
- report.json、deliverables.json、docs/run-summary.md 由底座維護。封裝排除 outputs，更新保留既有成果。
