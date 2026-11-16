# RoutineKeel 開發 handoff

## SNAPSHOT

- 目標：依 `C:\Users\allan\Downloads\RoutineKeel_flow_base_spec_v1.2.2.zip` 的 Stage 0–13 與 `devnote:devloop` 逐階段實作；Stage 0–7 已提交，Stage 8 已實作驗證待提交，Stage 9 尚未開始。
- 基準 HEAD `4f7ecf25`；Stage 0 commit `490ed4d`、Stage 1 `21fec1f`、Stage 2 `7c73f2f`、Stage 3 `b4d3179`、Stage 4 `fd9de34`、Stage 5 `6afbfbc`、Stage 6 `53407bb`、Stage 7 `8343136`。
- 工作樹原有 `.idea/` 七個暫存檔案屬使用者變更，勿修改或納入後續提交。

## DECISIONS

- 保留 Python／uv／SQLite／FileLock、Local-first、無常駐 HTTP；底座不修改字幕領域邏輯。
- Stage 1 保留 legacy SQLite 四表，新增 v001 schema 與一致性備份；舊 runs 透過原 facade 讀寫並以 view 顯示推定狀態，原值仍保留。
- Stage 2 使用 `RunContext`/`ContextVar`，移除核心路徑對共享 `run_logdir` 的依賴；`action_context` 保留相容屬性。
- Stage 3 新增 v002、Run／Step Attempt CAS 狀態機、單工作區寫入認領、idempotency、heartbeat／lease、程序身分辨識與重開 reconcile；現有 Workflow／Demo／Action 已寫入新狀態表。另有按需啟動的獨立 `start_detached` supervisor，Client 退出後可接管保留的 run_id；既有同步 CLI 保持可用。
- Stage 4 保留 Reporter 的 `events.jsonl` 首尾相容順序；新增 `flight.jsonl` 結構化事件、span/parent 與程序身分、fsync 與 checkpoint、索引及 cursor 查詢、輪替／截斷標記。Worker、資源、節點、交付與診斷持久化失敗均有事件；詳見 `routinekeel/flow_base/docs/event-trace.md`。
- Stage 5 新增 `observability/diagnostics.py`：證據決定根因確定程度、安全分享 allowlist、次要故障與 `parent_error_id`；既有 `diagnostics.py` 保持 facade 並加入 run log→DB→最小 stderr fallback。Engine／Action／launcher／CLI／supervisor 的失敗收尾以 reporting boundary 保留原始例外，詳見 `routinekeel/flow_base/docs/diagnostic-boundary.md`。
- Stage 6 的 `Runtime.run_process` 保留 facade，控制核心移至 `execution/process.py`；加入大小受限且可輪替的合併輸出、程序身分／執行時間／peak RSS／退出碼證據、timeout→grace→force stop、Windows Job Object 及 POSIX 短命 guardian。Worker env 只傳必要 OS／運算設定，不繼承宿主密鑰；詳見 `routinekeel/flow_base/docs/process-control.md`。
- Stage 7：`Step` 新增 cacheable／side_effects／manual retry／output_artifacts，Manifest 與 Workflow 可宣告相符的具名 terminal outputs；`storage/artifacts.py` 驗證多檔案 manifest 與 sidecar，缺檔不命中快取。`storage/delivery.py` 以同磁碟 staging、immutable versions、原子 current.json 指標與可恢復 journal 發布；DB staged→published 與 Run succeeded 在同一交易。舊根路徑保留可回滾鏡像，失敗與取消不破壞前版；提供舊版重新交付與明確確認後的 GC。SQLite v003 加入 Step ArtifactSet 關聯；詳見 `routinekeel/flow_base/docs/artifact-delivery.md`。
- Stage 8 新增 `observability/errors.py`，明確覆蓋 93 個底座 FlowError alias 與 13 個規格標準碼，保留既有 error.code；`observability/commands.py` 提供 doctor、diagnose、logs、explain-error、export-diagnostics 的離線查詢／安全預覽。CLI 新命令支援 human/json、0/1/2/3 退出碼；舊 CLI envelope 未改，新增繁中說明。doctor 使用 SQLite immutable 唯讀檢查，匯出需明確 confirm 與新檔名，無網路傳送；詳見 `routinekeel/flow_base/docs/error-catalog.md`。

## VALIDATION

- 原始 HEAD Windows 全測：`197 passed, 15 failed, 10 skipped`，失敗主要因缺少 `sh`、Windows 路徑／ZIP mode、字幕隔離程序編碼等；詳見 `routinekeel/flow_base/docs/upgrade-baseline.md`。
- Stage 1 封裝成功，完整回歸 `205 passed, 15 failed, 10 skipped`；Stage 2 封裝成功，完整回歸 `208 passed, 15 failed, 10 skipped`。Stage 0–2 新增測試 `11 passed`。
- Stage 3：封裝成功，完整回歸 `225 passed, 15 failed, 10 skipped`，15 項與原始失敗清單相同；新增測試涵蓋 100 次 CAS／claim 競爭、雙程序認領、程序身分與 kill/reopen、取消、v001→v002 升級、快取 Attempt 及獨立 supervisor 在 Client 退出後完成。
- Stage 4：封裝成功，完整回歸 `239 passed, 15 failed, 10 skipped`，仍是同一 15 項原始失敗；`tests/test_event_trace.py` 14 passed，相鄰測試組合 54 passed。曾發現舊 Action 要求 `events.jsonl` 首尾固定，已改為獨立 `flight.jsonl` 修正。
- Stage 5：封裝成功，完整回歸 `252 passed, 15 failed, 10 skipped`，仍是同一 15 項原始失敗；Stage 5 與相鄰診斷／事件／生命週期測試組合 `48 passed`。故障注入涵蓋磁碟滿、唯讀、SQLite locked、JSON 失敗、Reporter、CLI、Action、例外鏈與秘密標記。
- Stage 6：封裝成功，wheel 含 `execution/process.py` 與 `execution/posix_guard.py`；Windows 完整回歸 `271 passed, 15 failed, 10 skipped`，15 項仍是原始基線。Windows 程序故障／Worker env 測試 `18 passed`；Linux WSL 直接原始碼與發行 wheel 的 stdlib smoke 各 `6 passed`，涵蓋 Worker／子程序樹逾時、取消與父程序突然死亡。macOS 未實測。
- Stage 7：封裝成功，完整 Windows 回歸 `328 passed, 15 failed, 10 skipped`，失敗清單與 Stage 0 基線相同；Stage 7 專項 `tests/test_artifact_sets.py tests/test_atomic_delivery.py tests/test_db_migrations.py` 共 `65 passed`，含 20 故障點、磁碟／DB 故障、強制終止後重開、取消競爭、兩終點、多 sidecar、rollback、GC 及搬移工作區。提交 `8343136`。
- Stage 8：封裝成功，完整 Windows 回歸 `341 passed, 15 failed, 10 skipped`，仍是同一 15 項原始失敗；`tests/test_diagnostic_cli.py` 13 passed，與既有診斷相鄰測試組合 32 passed。覆蓋每個底座錯誤碼的 Catalog 契約、代表性真實錯誤、無 Run 的 launcher error_id、doctor 唯讀、JSON 無雜訊、匯出秘密標記與診斷 fallback。
- Windows 預設 Python 3.10 不符合專案要求。用 `UV_CACHE_DIR=E:\PycharmProjects\RoutineKeel\.uv-cache` 與 `uv run --locked --python 3.12 --project routinekeel/flow_base --group dev python tools/release.py` 打包；測試可用 `routinekeel/flow_base/.venv/Scripts/python.exe -m pytest`。

## LANDMINES

- `git` 寫 `.git` 需 sandbox escalation；每階段用 `git commit --only -- <明確檔案>`，避免帶入 `.idea/`。
- 原規格 ZIP 未放工作區，從本機路徑用 Python `zipfile` 讀取。原對話的 sandbox 連結在此環境不可直接讀。
- 本機 Windows 全測既有的 15 項失敗不是本輪重構引入，包含 POSIX `sh` 測試在 Windows 路徑、ZIP mode 與字幕隔離程序編碼；Stage 12 須處理或正式區分平台測試矩陣。
- Stage 7 限制：`current.json` 與 immutable version 是正式一致快照；舊 `output/` 多檔案鏡像以 journal 恢復，強制終止後至工作區重開前可能短暫呈現混合檔案。macOS 同磁碟 rename 尚未實機驗收。Stage 8 下一步依技術規格 10.2／10.5 建立 Error Catalog、診斷 CLI、繁中白話說明與修復動作，沿用現有錯誤碼 alias，不能破壞舊 CLI JSON。
- Stage 9 規格已讀：建立 `core/service.py`、`core/types.py`，提供 open_project/list_workflows/describe/plan/start/get_run/events/cancel/diagnose/artifacts/export/retry。start 接受 idempotency_key 與 execution_spec_hash，長工作交給 Stage 3 獨立 supervisor；plan 無副作用；Core 統一授權與路徑界線。CLI 需漸進改為 Service thin adapter，保持舊 JSON／退出碼。Stage 9 不新增 HTTP/MCP。下一步先確認 `Workspace.discover`／`Task.configuration` 的既有副作用，分離真正無副作用 plan 與 start，並用直接 Service／CLI 同動作對照測試。

## HISTORY

- 2026-11-17：完成 Stage 0–2；Stage 3 開始。
- 2026-11-17：Stage 3 實作與驗證完成，含 Client 退出後獨立 supervisor；準備進入 Stage 4。
- 2026-11-17：Stage 4 實作與驗證完成，準備進入 Stage 5。
- 2026-11-17：Stage 5 實作與驗證完成，準備進入 Stage 6。
- 2026-11-17：Stage 5 提交；Stage 6 完成 Worker env 白名單切片，程序控制仍待完成。
- 2026-11-17：Stage 6 完成 Windows／WSL Linux 程序故障驗證，準備進入 Stage 7。
- 2026-11-17：Stage 6 提交；Stage 7 讀取規格、盤點正式輸出與快取風險。
- 2026-11-17：Stage 7 完成契約、ArtifactSet 與 staging clone 切片；正式 promotion／rollback 尚待實作。
- 2026-11-17：Stage 7 版本交付、rollback、GC 與故障矩陣驗證完成，準備進入 Stage 8。
- 2026-11-17：Stage 7 提交；Stage 8 讀取 Error Catalog 與 CLI 規格。
- 2026-11-17：Stage 8 Error Catalog／診斷 CLI 完成驗證，準備進入 Stage 9。
