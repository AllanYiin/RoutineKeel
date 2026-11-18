# RoutineKeel 整合驗收紀錄（Stage 12 三平台 CI 通過）

本報告依 `RoutineKeel_flow_base_spec_v1.2.2.zip` 的 AC-01～08、M1～M8，以及使用者後續核定的 `specs/mcp-queue-v1.md` 修訂，記錄實際證據。Stage 10 已改為持久化 `execution_id` 與另行啟動的 queue-worker；MCP Client 不直接派生執行程序。三平台 CI 已通過；舊版 wheel 回滾已按下述範圍實測。使用者將 macOS Finder／Tk 實機驗證延後；非技術真人操作走查仍未完成。

## 測試環境與重現命令

- 觀測日：2026-11-17；Windows 11 build 26300；CPython 3.12.11；Intel64 Family 6 Model 198 Stepping 2；24 logical CPUs；實體記憶體約 95.4 GiB。
- 專案位於 `E:`；受控唯讀查詢顯示 E: 位於 StoreJet USB 外接磁碟，不能把 E: 的數值當作 SSD 測試。`C:` 位於 `NVMe Micron_2500_MTFDKBA1T0QGN`；M7 另在已授權的 C: 暫存根量測，測後自動清理。
- 發行建置：`$env:UV_CACHE_DIR='E:\PycharmProjects\RoutineKeel\.uv-cache'; uv run --locked --python 3.12 --project routinekeel/flow_base --group dev python tools/release.py` → `Valid: YES`，三個 ZIP 建置成功；`SHA256SUMS` 三個雜湊逐檔核對正確。
- 完整 Windows 回歸（含 v004 缺表與排隊輸入漂移故障案例）：`routinekeel/flow_base/.venv/Scripts/python.exe -m pytest tests routinekeel/flows -q --tb=no --disable-warnings` → **382 passed、21 skipped、0 failed**，耗時 212.82 秒。11 個 POSIX shell 案例因 Windows 明確 skip，其餘 10 個為既有平台 skip。
- WSL Linux 交叉檢查：在 `OpenClawGateway` 的 `/tmp` 複製目前工作樹（排除 Windows `.venv`、`dist`、`.git`），以 `tools/uv-assets.json` 鎖定的 uv 0.13.6 Linux wheel 執行前先核對 SHA-256；`uv run --locked --python 3.12 --project routinekeel/flow_base --group dev python tools/release.py` → `Valid: YES`、三 ZIP 成功；`uv run --locked --python 3.12 --project routinekeel/flow_base --extra mcp --group dev pytest tests routinekeel/flows -q --tb=no --disable-warnings` → **390 passed、13 skipped**，94.33 秒。WSL 暫存副本測完即清理；這不是 GitHub Actions 或 macOS 實機結果。
- MCP SDK Client：`python -m pytest tests/test_mcp_stdio.py -q` → **5 passed**，涵蓋新版／legacy 協商、Client 關閉重連、Worker 未啟動先排隊、取消、路徑遮罩及 Windows process netstat 無 TCP listening port。`tests/test_execution_queue.py tests/test_mcp_stdio.py tests/test_integration_mcp_cli.py` 合計 **11 passed**（加入 netstat 測試後）。
- M7：在 v004 上執行 `routinekeel/flow_base/.venv/Scripts/python.exe tests/bench_run_status.py --root <C: 已授權暫存根>` → C: NVMe 上 5,000 Run、200 次查詢，p50 **8.82 ms**、p95 **9.78 ms**、max **15.07 ms**，低於 200 ms；不含 Worker 啟動。E: USB 外接磁碟同法 p95 **21.91 ms**，只供比較，不作 SSD 門檻證據。
- Windows 發行 ZIP 冷啟動：在工作區一次性乾淨目錄解壓 `flow_base-0.2.11.zip`＋`flow_copy-0.2.2.zip`，執行 `run_app.bat list` → exit 0、可見 `ai_flow02`；執行 `run_app.bat run --task ai_flow02 --input <檔案> --output <成果>` → exit 0、成果內容正確；`run_app.bat doctor --format=json` → exit 1（資源狀態 unknown 的預期診斷碼，JSON 含 checks）。首次 sandbox 內 uv 解壓 Python 被 ACL 拒絕；在受控非 sandbox 的同機重測通過。冷啟動揭露 wheel/`__version__` 原本不一致，已修為 0.2.11 並在 release.py 加入建置前比對。
- 乾淨 0.2.12 發行包另完成 Windows 端到端測試：`start.ps1 --install-mcp` exit 0、已安裝 wheel 回報 0.2.12；由另一個程序啟動 queue-worker，官方 SDK Client 提交後關閉再重連，查得 `succeeded`、同一 `execution_id` 關聯的 `run_id` 與正確成果，Worker exit 0。
- 回滾指引的一次性資料演練：以舊四表建立測試 DB，升至 v004 後驗證 `before-v001` SHA-256，再用 SQLite backup API 還原；原始基準 commit `4f7ecf25` 的舊版原始碼可讀取 `run_old`。此步單獨只驗證舊原始碼；實際 wheel 結果列於下一項。`before-v004` 不適用於該舊四表程式。
- 實際舊 wheel 演練：本機 `flow_base-0.2.9.zip` 的 ZIP 路徑安全、wheel SHA-256 與包內 `start.ps1` 期望值相符。將 `before-v001` 還原到一次性工作區後，用未修改的舊 `start.ps1` 搭配 PowerShell 7 安裝，`history` exit 0 並讀到 `run_old`。同一包 `run_app.bat` 在此 Windows PowerShell 5.1 環境因 `Get-FileHash` 缺失 exit 1；不把 PowerShell 7 成功推論為舊雙擊入口通過。
- [PR #3](https://github.com/AllanYiin/RoutineKeel/pull/3) 最後文件 head `264a605` 的 [Actions run #12](https://github.com/AllanYiin/RoutineKeel/actions/runs/43993065956)：Ubuntu **401 passed、3 skipped**，三個 ZIP SHA256SUMS 正確；macOS 15 **402 passed、2 skipped**；Windows 2025 **383 passed、21 skipped**。三個測試 job success，publish skipped。PR 其後由儲存庫擁有者合併；合併後 [run #13](https://github.com/AllanYiin/RoutineKeel/actions/runs/43994026339) 的三平台 job 和 publish job 均 success，產生 `build-00fff1c` Release。這項自動發布發生在真人走查回報之前，不能視為人工 Gate 通過。

## AC 驗收

| 項目 | 現況 | 證據與缺口 |
|---|---|---|
| AC-01 Run 隔離 | 三平台 CI 通過 | `test_integration_recovery.py` 以 4 個 caller 完成 100 次混合操作（90 Action、10 個不同輸入 Run），逐項核對唯一 run_id、Run 事件、Action marker 與成果檔，誤歸屬 0 件；另有 100 次 claim/CAS 與多程序 event writer 測試。 |
| AC-02 中斷恢復 | Windows／Linux 具恢復證據 | Stage 3 的 kill/reopen、Stage 7 journal 恢復與新版 `test_worker_reconnects_run_reserved_before_queue_link_is_saved` 通過；Client 關閉不再是 Worker 父程序，重連查詢可取得原 `execution_id` 的結果。 |
| AC-03 診斷次故障 | 通過 | `test_diagnostics_fallback.py`、`test_event_trace.py` 的磁碟／DB／Reporter 故障測試保留主例外。 |
| AC-04 多產物快取 | 通過 | `test_artifact_sets.py` 遺失 sidecar 與 hash 錯誤時拒絕 cache hit。 |
| AC-05 MCP stdio | 三平台 CI 通過 | `test_mcp_stdio.py` 新版／legacy SDK 協商、七工具、路徑限制、Client 關閉重連、Worker 晚啟動與取消通過；`test_integration_mcp_cli.py` 證明 CLI/MCP 的 execution_id、run_id、狀態與成果一致；Windows `netstat` 對 MCP Server PID 無 TCP listening port。 |
| AC-06 專案續編與預覽 | Windows UI 測試通過，macOS GUI 延後 | `test_ui_contract.py` 以 Tk 按鈕完成範例、重開與複製專案後查舊成果；PNG/GIF、UTF-8 預覽與等比縮放；不支援格式明示原因。macOS headless runner 的 Finder／Tk 視窗操作未覆蓋，依使用者決定延後實機驗證。 |
| AC-07 敏感資訊 | 通過（本機匯出） | `test_security_diagnostics.py` 同時注入 API key、Authorization、prompt、文件內容四類標記，分享包零洩漏，保留 `error_id`／錯誤碼；外部目標未授權時拒絕。 |
| AC-08 LLM Streaming | 未啟用 | 本機診斷與 UI 不提供可選 LLM 說明；普通工作流不依賴 LLM。未宣稱已實作 Streaming 說明。 |

## M 指標

| 指標 | 現況 | 證據與缺口 |
|---|---|---|
| M1 已知錯誤解釋 | 通過既有 Catalog 測試 | `test_diagnostic_cli.py` 覆蓋底座錯誤碼與繁中提示；整合介面仍需抽查。 |
| M2 Run/Step 追蹤 | 三平台 CI 已測路徑 100%，未知故障邊界不外推 | 正式 Workflow、Action、Demo 的 Step Attempt 成功／失敗都有同 ID 的 `step.started` 與 `step.finished`／`step.failed` 測試；100 次 Run／Action 混合案例逐項核對前後事件與 run_id，缺漏 0 件。原始碼中三處 `step.started` 均先建立 Attempt；完全無法落盤等硬體故障由 M2 原規格排除。 |
| M3 100 次互不污染 | 通過（Windows） | `test_integration_recovery.py` 100 次實際 Run／Action 混合交錯，執行約 68 秒；Run ID、事件與 10 個不同成果標記誤歸屬 0 件。 |
| M4 交易式交付 | 通過 20 故障點 | `test_atomic_delivery.py::test_failure_during_promotion_restores_previous_result` 的 20 個 `fault` 參數，加上 DB／磁碟／取消與 crash journal 測試；`test_integration_atomic_publish.py` 證明失敗的第二次交付不改變 Service 可見的舊成果。 |
| M5 強制終止後恢復 | Windows／Linux 具恢復證據 | Stage 3 supervisor 身分／lease 測試、Stage 10 Worker 在 `run_id` 寫回佇列前崩潰後以 idempotency 領回測試均通過；MCP Client 關閉後由獨立 Worker 繼續完成。Worker／OS 同時強制終止的全組合未額外實測。 |
| M6 分享診斷無洩漏 | 通過（本機匯出） | `test_security_diagnostics.py` 四類標記於分享 JSON 中 0 次出現，且保留安全錯誤碼與 ID。 |
| M7 5,000 Run 查詢 | Windows NVMe 本機達標 | v004 的 `tests/bench_run_status.py` 在 C: NVMe 5,000 Run／200 次查詢 p95 **9.78 ms** < 200 ms；E: USB 外接磁碟結果不當作 SSD 證據。 |
| M8 原流程回歸 | 三平台 PR CI 通過 | Ubuntu 401 passed／3 skipped、macOS 402 passed／2 skipped、Windows 383 passed／21 skipped，三平台 build/test success；Ubuntu SHA256SUMS 三檔正確。Windows 11 個 POSIX shell 案例條件 skip；macOS Finder／Tk 真人操作另列未覆蓋。 |

## 平台與 CI

`.github/workflows/packages.yml` 的 Ubuntu 24.04、`windows-2025`、`macos-15` 三平台 Gate 在 PR 最後 head 與合併後 run 均 success。合併後自動發布揭露原工作流缺少真人 Gate；[PR #5](https://github.com/AllanYiin/RoutineKeel/pull/5) 已合併至 `main`，後續只有在 `main` 上手動觸發且明確勾選 `publish_release` 時才執行 publish，並仍等待三平台 job。另有只手動觸發的 `.github/workflows/platform-checks.yml`；它尚未單獨執行。macOS Finder／Tk GUI 與官方 MCP Inspector 尚未執行。

下一步：完成發行包使用手冊的非技術真人走查。macOS Finder／Tk 實機驗證依使用者 2026-11-18 決定延後，不計本輪開發迴圈結束條件，但仍保持未驗證。使用者已選擇保留先前自動發布的 Release；發行控制修訂的三平台 CI 已通過且 PR #5 已合併。Stage 13 人工 Gate 在取得走查證據前維持未通過。
