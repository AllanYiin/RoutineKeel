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
