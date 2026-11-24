# RK-NEXT 開發基線

2026-11-22 於 Windows 從儲存庫根目錄驗證。依據 RK-NEXT-2026-11 的 `01_Technical_Spec.md`（SHA-256 `92556bf77a1437ff5213c578130dd59cada91c5d8f5c3b6cb8a135a88e487550`）及 `03_Codex_Stages.md`（`6c81eacef9cb97a3ecc53ed2c9e6f3fa872ac5f067e5831127e25040dcbc9a32`）。

規格所引 main 為 `426ba934`。本次測試對象是 `codex/workflow-zip-updates` 的工作樹，HEAD 為 `ad8dbcb9f141502a7c6b5733bbbb93eda873a826`。開始時已有目錄搬移、未追蹤的根層元件與已暫存的 IDE 檔案，單獨 checkout 此 HEAD 無法重建本次受測內容。

| 相容邊界 | Stage 0 實作位置與行為 |
| --- | --- |
| 執行入口 | `flow_base/src/flow_base/cli.py`、`adapters/mcp_stdio.py` 透過 `core/service.py` 操作，`execution/queue_worker.py` 處理 SQLite 佇列。保留 CLI 回傳格式、execution_id、run_id 及具名產物。 |
| 藍圖 v1 | `flow_base/src/flow_base/blueprints/{source,recipe,ir,installer,sessions}.py`。依步驟順序解析產物並試做；此基線的 `check_with_studio` 在找不到 Studio 時回傳 `not_installed`。此項是 Stage 1 驗收對象。 |
| Studio | `workflow_studio/src/workflow_studio/{conversation,ir,compiler,verifier}`。預設 guide 由宿主顯示；既有 IR 驗證 DAG，編譯與藍圖安裝各有入口。 |
| Runtime | `contracts.py` 定義 Step.needs，`engine.py` 執行已排定的步驟。既有線性藍圖與已安裝 Adapter 都是回歸對象。 |
| 封裝 | `tools/release.py`、`tools/studio_release.py` 產出底座 0.2.15、Studio 0.2.0、字幕 0.5.0、複製 0.2.3 及完整工作區 ZIP。檢查入口為 `tests/test_source_layout.py`、`tests/test_release_layout.py`、`tests/studio/test_distribution_package.py`。 |

## 資料與還原

底座 `state.sqlite3` 由 `storage/migrations/v001.py` 至 `v004.py` 維護。舊版 flows、runs、steps、requests 與後續修訂、job、run_executions、step_attempts、artifact_sets、deliveries、diagnostics、event_index、execution_queue 共存。Studio 獨立的 `studio.sqlite3` 版本 14，管理專案、修訂、來源、工具、證據、測例、建置、驗證、核准與發行。完整表結構以兩套 `storage/migrations` 為準。

新增資料表時，由資料的擁有元件新增序號 migration。初始資料在同一交易內建立，以穩定識別避免重複。舊資料缺少真人決策時保留未知。先對停寫的測試副本取得 SQLite backup，核對 SHA-256、integrity_check、schema 版本與代表性資料列，還原到另一個空目錄後用相容版本重開。還原整個工作區時，連同相同版本的流程、檔案成果及狀態一起還原。舊 DB 的唯讀檢查使用 SQLite URI `mode=ro`，開啟 Runtime 可能觸發遷移。Stage 0 僅操作測試資料。

## 重跑本機驗證

使用 CPython 3.12、uv 與專案 lockfile。Studio 的選配設定名稱見 [環境範本](../../workflow_studio/.env.example)。guide 和這組測試不需要模型金鑰。以下封裝步驟適用於獨立開發副本，會重建該副本的 dist。

```powershell
uv run --locked --project flow_base python tools/release.py
uv export --frozen --project "workflows/FLOW_課程影片產生字幕/src/ai_flow01/profiles/asr-cpu" --no-dev --no-emit-project --output-file dist/subtitle-test-requirements.txt --quiet
uv run --locked --project flow_base --extra mcp --group dev --with-requirements dist/subtitle-test-requirements.txt python -m pytest tests workflows --ignore=tests/studio -q -rs --junitxml=dist/v122-stage0-runtime.xml
uv run --locked --project workflow_studio --group dev python -m pytest tests/studio -q -rs --junitxml=dist/v122-stage0-studio.xml
uv run --locked --project workflow_studio --group dev ruff check --config workflow_studio/pyproject.toml workflow_studio/src workflow_studio/scripts tests/studio
uv run --locked --project workflow_studio --group dev ruff format --check --config workflow_studio/pyproject.toml workflow_studio/src workflow_studio/scripts tests/studio
```

封裝程序會在隔離副本先產生 launcher，再執行 APSM strict。直接在未生成 launcher 的原始碼目錄執行 strict 會報缺三個 run_app 檔。資料庫演練位於 `tests/test_db_migrations.py` 與 `tests/studio/test_migrations.py`。Stage 0 回滾只需還原本次說明及 migration 測試的修改區塊。

## 2026-11-22 實測紀錄

| 檢查 | 結果與重跑條件 |
| --- | --- |
| Windows 底座、MCP、兩個工作流 | 531 passed、28 skipped。報告 `dist/v122-stage0-runtime.xml`。 |
| 上述 28 個條件略過案例 | 21 個 POSIX 案例在 Ubuntu 24.04 WSL、PowerShell 7.4.13 通過。初次 20 過 1 敗，補上 CI 所用的 uv PATH 後 21 個全部重跑通過。另外 7 個降噪案例在鎖定的 demucs-cpu 環境通過。報告為 `dist/v122-stage0-posix.xml`、`dist/v122-stage0-denoise.xml`。 |
| 原始碼布局、公開契約、SQLite | 17 passed。舊 DB 備份以唯讀開啟，實際寫入遭拒，復原後保留舊版資料列且原備份雜湊相同。報告 `dist/v122-stage0-baseline.xml`。 |
| Blueprint v1 及 Studio 契約、遷移、分包專項 | 分別 6 與 31 passed。報告 `dist/v122-stage0-blueprint.xml`、`dist/v122-stage0-studio-contract.xml`。 |
| 新建候選發行包 | 在獨立 dist 子目錄產出五個 ZIP。APSM strict 通過；設定 `ROUTINEKEEL_TEST_RELEASE_DIR` 指向候選 release 後，13 個布局、CLI、快取及保護目錄案例通過。報告 `dist/v122-stage0-candidate-layout.xml`。 |
| Studio Ruff | lint 通過，158 個檔案符合格式。 |
| Studio 全套 | Windows 519 passed、0 skipped、0 failed，852.04 秒。報告 `dist/v122-stage0-studio.xml`。 |

依測例名稱比對，底座及工作流收集的 559 例都在至少一個適用環境通過，Studio 另有 519 例通過。Stage 0 的基線與回復條件已驗收，後續階段另立測試證據。

## Stage 1 必要驗證

底座與 Studio 分別安裝隨包提供的 `routinekeel-contracts` wheel，共用同一份 IR 語意及 RECORD 驗證。兩個環境仍各自啟動。試做與安裝均執行必要驗證，未取得目前政策的 PASS 收據須回到草稿重驗。原有已安裝流程可繼續執行。程式與驗收入口見 `flow_base/components/workflow_contracts/`、`blueprints/ir.py`、`blueprints/installer.py`、`tests/test_blueprint_install.py` 與 `tests/test_release_layout.py`。

Windows 安裝及更新測試 80 passed，v1 與媒體等專項 19 passed，Studio 契約／Verifier／相容及封裝專項 30 passed，候選 ZIP 布局及載入 15 passed，WSL 啟動器 21 passed。報告分別為 `dist/v122-stage1-{install,v1-final,studio,final-layout,posix}.xml`。Ruff 166 個檔案通過。候選包實際執行 `start_studio.ps1 --prepare-only`，建立兩個獨立環境、完成依賴檢查。此階段沒有 schema 遷移；回退使用前版發行包運行舊流程，暫停新安裝直到有具備必要驗證的版本。

## Stage 2 與 Stage 3

Graph v2 格式與 v1 轉換見[圖形契約](blueprint-graph-v2.md)。Stage 2 相關回歸 31 passed、Studio IR 26 passed；最終固定排序後 Graph 專項 15 passed。

後續邊界複查發現，v1 上限 200 步若每步都有校閱，等價轉換需要 401 個節點；`is_step` 另代表步驟本身由人執行。已在 2.1 契約修正，保留 2.0 限制、舊來源 SHA、具名輸出及原埠型別。追加驗證為 Graph 專項 16 passed，Graph／v1／安裝回歸 32 passed，見 `dist/v122-stage2-v21.xml` 及 `dist/v122-stage2-v21-regression.xml`。

Stage 3 使用 `StudioService.interviews` 與 Studio schema 15。具名請求重送回同一修訂，過期及並行衝突保留已確認版本。上游修改使相關規則與測例待確認。41 個訪談、專案、歷史、測例交易與遷移測試通過，見 `dist/v122-stage3-final.xml`。回復舊版時停止寫入，保留當前狀態，將升級前 v14 備份與中繼資料還原至空目錄，搭配舊版重開。schema 15 新增的訪談歷史保留在新版工作區。

## Stage 4 對話入口驗收紀錄

依使用者最新決定，採信既有桌面對話作為回覆來源。宿主顯示問題及草稿，將下一則實際使用者回覆連同 question_id、專案版本與 request_id 保存。文字直接回答、採納已顯示的草稿、拒絕及稍後處理都能在同一段對話內完成。此處採宿主轉交的發言來源，不另外做身分認證。新版對話專項見 `dist/v122-stage4-chat-facade.xml`（19 passed）與 `dist/v122-stage4-chat-mcp.xml`（6 passed）。四步自動流程的實際試做顯示，無校閱時等待 0 次，只指定交付前校閱時等待 1 次，見 `dist/v122-review-points.xml`。

| 入口或條件 | 證據 |
| --- | --- |
| 純文字問題、拒絕、修正、暫停、真實串流、預覽 | `dist/v122-stage4-facade.xml`，18 passed。加入未知檔案的安全本機定位及建立請求去重後，`dist/v122-stage4-retry.xml` 另有 11 passed。 |
| MCP stdio 協商與重連 | `dist/v122-stage4-mcp.xml`，6 passed。真實用戶端建立、讀取、送出草稿及重連，直接指定 confirmed 會被拒絕。 |
| 封裝與啟動 | `dist/v122-stage4-layout.xml`，15 passed。候選包 prepare-only 建立獨立環境，從包內 Runtime 建立訪談再由另一個程序查詢，確認數為零，見 `dist/v122-stage4-cold-start.json`。 |
| POSIX 與 Windows 互動啟動器 | `dist/v122-stage4-posix.xml`，22 passed，包括確認內容不寫入 PowerShell transcript。 |
| 完整回歸首輪揭露的問題 | 新增的診斷碼已補中文說明與處置。Windows Job Object 測試分開監督程序的冷啟動與 worker 的五秒啟動期限，直接觀察正式 pid 與 Job Object 紀錄，保留三秒回收斷言。修後專項 `dist/v122-stage4-regression-fixes.xml`，30 passed。 |

設定 `ROUTINEKEEL_TEST_RELEASE_DIR` 時，測試使用相同建置下的 release、workspace 與 wheels，正式發行副本可留在原位。Stage 4 沿用 Studio schema 15。回復 Stage 3 的 CLI 與 MCP 入口後，訪談快照仍可用 Stage 3 程式讀取。本機訪談確認紀錄僅適用於當時呈現的內容與版本。

完整重跑時 Windows 底座／流程為 552 passed、30 skipped，見 `dist/v122-stage4-same-build.xml`。其中 23 個平台案例在 WSL 通過、7 個音訊案例在 demucs-cpu 通過，兩份 JUnit 分別為 `dist/v122-stage4-posix.xml` 與 `dist/v122-stage4-denoise.xml`。Studio 全套收集當時 529 passed。之後新增的最大 v1 藍圖與建立訪談重送案例均已另行通過。以最新收集清單的檔名與完整參數化測試名逐案核對，共 583 個底座／工作流及 530 個 Studio 案例可對應實際 PASS，見 `dist/v122-stage4-coverage.json`。這是跨執行環境的逐案覆蓋。桌面對話回覆與關鍵校閱的改動另有上列新版專項。
