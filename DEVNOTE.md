# DEVNOTE

## SNAPSHOT — 當前狀態

最後更新：2026-11-17T07:30+08:00
交接狀態：verified

### 目標與授權範圍

依 RoutineKeel 底座階段規劃及使用者最新 MCP 佇列修訂，完成 Stage 0–13 並逐階段驗證提交。

### 目前進度

Stage 0–9 已提交，Stage 9 9ead2a4；Stage 11 Tk UI 已提交 ad753e0。Stage 10 依使用者最新「提交→execution_id→SQLite佇列→獨立 queue-worker→按 ID 查詢」規劃完成 Windows／WSL Linux 本機驗證，準備獨立提交。Stage 12/13 整合與文件草稿仍未達 macOS／GitHub CI／發行 Gate，未提交。原有 .idea/ 七個 staged 檔案屬使用者變更。

### 下一步

用獨立 Git index 只提交 Stage 10 檔案並排除 Stage 12/13 WIP 與 .idea；再依修訂規格完成 Stage 12 跨平台／正式 CI 與 Stage 13 文件、冷啟動及交付 Gate。

### DECISIONS

- MCP stdio 只做驗證、入列、用 execution_id 查進度／結果／取消；run_id 由獨立 queue-worker 實際啟動時建立。MCP Client 不派生執行程序。
- queue-worker 由使用者另行啟動，單工作區 FileLock、SQLite v004 佇列、交易認領與 execution:idempotency；未啟動時明示 queued。無 HTTP、無自動安裝 OS 服務。
- 工作區內路徑及已安裝流程為 MCP 第一版界線；外部附件先匯入工作區；原生 MCP Tasks 延後到宿主能力確認。
- flow_base 版本升至 0.2.12；發行包含 queue_worker 三平台入口、mcp-local.md 及選配 --install-mcp。

### LANDMINES

- macOS 真機／Finder、GitHub Actions 原 CI 與手動矩陣仍未執行，Stage 12/13 不可宣稱完成。
- M7 Windows 5,000 Run p95 32.16ms，但 E: SSD 屬性因 CIM 被拒未知。
- Stage 12/13 WIP 與 .idea staged 必須在 Stage10 提交時排除；資料庫 v004 回退舊版須還原 before-v004 備份。

### 證據索引

- routinekeel/flow_base/specs/requirements.md Stage10 修訂與 specs/mcp-queue-v1.md
- src/flow_base/core/queue.py、execution/queue_worker.py、storage/migrations/v004.py、adapters/mcp_stdio.py
- tests/test_execution_queue.py、tests/test_mcp_stdio.py、tests/test_integration_mcp_cli.py
- dist/release/flow_base-0.2.12.zip 手動 wheel/launcher/source 檢查及乾淨包端到端
- git status 顯示 Stage12/13 WIP 與使用者 .idea staged

### VALIDATION

- Stage10 Windows 專項與完整回歸 — PASS：tests/test_execution_queue.py tests/test_mcp_stdio.py tests/test_integration_mcp_cli.py 共 10 passed；新 netstat 無 TCP listening port 1 passed；完整 Windows 379 passed、21 skipped、0 failed。
- Stage10 WSL Linux 完整回歸 — PASS：OpenClawGateway /tmp 隔離副本建置 Valid YES、387 passed、12 skipped；在 Windows-only netstat 測試加入前，該測試於 Linux 條件 skip。
- Stage10 發行包端到端 — PASS：乾淨 flow_base 0.2.12 ZIP 的 --install-mcp exit0、wheel import 0.2.12、獨立 Worker + 官方 SDK 提交後關閉 Client 並重連查到 succeeded/run_id/正確成果，Worker exit0。
- SQLite v004 — PASS：tests/test_db_migrations.py 11 passed，含 v003→v004 備份與舊 Run 不改寫；worker crash before queue link 後以同一 run_id 恢復。
- Stage12/13 macOS／GitHub CI — NOT_RUN：尚未推送或觸發 CI，macOS 實機不可用。

# HISTORY

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
- 2026-11-17：Stage 8 提交；Stage 9 盤點現有 plan/discovery/load 的持久化與程序副作用。


## [2026-11-17T04:45+08:00] Stage 9 Service 實作與回歸追查

<!-- devnote-review:efe90139af6dfc22315981b5 -->

完成 Service/CLI 主要委派、唯讀計畫與受限重試；安全重試衝突已修正，Stage 9 專項 13 passed，發行包建置成功。完整回歸仍多出失敗，待追查後才能提交 Stage 9。


## [2026-11-17T04:55+08:00] Stage 9 回歸恢復基線

<!-- devnote-review:25b9e9d2f2111cb6a6c5f016 -->

將 CLI/Service 對照測試置於獨立 Service client，並把插件快取修復測試改由 prepare 觸發，符合 plan 無副作用契約。全測 354 passed、15 個既有失敗、10 skipped；發行包含三個新 Core 模組。


## [2026-11-17T05:07+08:00] Stage 9 提交與 Stage 10 Windows Job 限制

<!-- devnote-review:faa46ef2050405cb5a147553 -->

Stage 9 提交 9ead2a4。Stage 10 的新版／legacy MCP 協商、工具列表、路徑拒絕與取消測試通過；Client 關閉／重連失敗，Windows SDK 會清理 Server 子程序樹。可選依賴沒有破壞一般 CLI 與打包。


## [2026-11-17T05:28+08:00] Stage 11 本機工作台完成驗證

<!-- devnote-review:bfc3f7f82aa9902c8f4ba1bf -->

建立 Tk 工作台與發行包 open_ui 入口；測試從 UI 按鈕完成範例並重開／搬移專案查舊成果。完整回歸除 Stage 10 未解測試外沒有新增失敗，POSIX ZIP 權限測試已修正。


## [2026-11-17T06:16+08:00] Stage 11 提交與跨平台整合驗收

<!-- devnote-review:659179117a844035887dbccb -->

Stage 11 提交 ad753e0。Stage 12 100 次混合 Run/Action、20 故障點、秘密遮罩與 5,000 Run 延遲取得證據；Windows 368 passed/1 failed/21 skipped，WSL Linux 378 passed/12 skipped。唯一 Windows 失敗仍是官方 MCP Client 關閉程序樹；Stage 13 文件草稿已標示未通過 gate。


## [2026-11-17T06:27+08:00] 跨平台回歸與 Windows 冷啟動修正

<!-- devnote-review:8d0ba1c753c9e704037e05cf -->

Windows 368 passed/1 MCP fail/21 skipped，WSL Linux 378 passed/12 skipped。乾淨 Windows ZIP 冷啟動揭露並修正 __version__ 與 wheel 不一致；bootstrap 改用 .NET SHA-256，冷啟動 list/run/doctor 通過。Stage12/13 文件與回歸草稿未完成，等待 Windows MCP 架構決定。


## [2026-11-17T06:41+08:00] Windows MCP 保留未完成的決定

<!-- devnote-review:490d43a69e684db2957a7556 -->

使用者明確選擇維持原規格、不新增獨立常駐服務；Stage10 Windows SDK Client 退出後原 Run 無法可靠續跑，MCP 專項 3 passed/1 failed。新增測試證明失聯 Run 在 lease 到期後可標為 interrupted 並保存事件，但不取代續跑需求。Stage12/13 草稿與 Gate 維持未完成。


## [2026-11-17T07:30+08:00] Stage 10 改為持久化佇列並完成本機驗證

<!-- devnote-review:ddadb3a34d453b3b76b4fbc5 -->

依最新使用者規劃修訂 Stage10：MCP 只入列與按 execution_id 查詢，獨立 queue-worker 執行，SQLite v004 建立 Run 前持久化 ID。Windows 全測 379 passed/21 skipped，WSL Linux 387 passed/12 skipped；乾淨發行包的 Client 關閉重連測試成功。Stage12/13 macOS與CI仍待。
