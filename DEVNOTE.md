# DEVNOTE

## SNAPSHOT — 當前狀態

最後更新：2026-11-17T07:55+08:00
交接狀態：verified

### 目標與授權範圍

依 RoutineKeel 底座階段規劃及使用者核定的 MCP 佇列修訂完成 Stage 0–13，逐階段驗證提交。

### 目前進度

Stage 0–11 已提交（Stage10 16d0fd6，Stage11 ad753e0）。Stage12 Windows／WSL Linux 本機整合與發行包建置已通過，測試報告與手動 Windows/macOS CI 矩陣是未提交候選；正式 GitHub Actions、macOS 與使用者走查未執行，Stage12 DoD 尚未完成。Stage13 README／架構／回滾／已知問題／release checklist 草稿未提交。原有 .idea/ 七個 staged 檔案屬使用者變更。

### 下一步

只提交 Stage12 本機整合候選（不宣稱完整 Stage12 DoD），保留 Stage13 草稿與 .idea。需取得使用者同意與可用 GitHub 認證後，推送候選並執行原 Ubuntu CI 與手動 Windows/macOS 矩陣；核對結果後完成 Stage12，最後處理 Stage13 發行 Gate。

### DECISIONS

- Stage10 MCP 僅入列與用 execution_id 查詢，獨立 queue-worker 執行；v004 佇列與可選 SDK 已提交。
- Stage12 對正式 Workflow／Action／Demo 補 Step Attempt 前後事件，100 次混合 Run/Action 各自核對 ID 與成果；20 交付故障點與四類秘密標記持續驗證。
- Windows POSIX sh 案例明確 skip，Linux 仍執行；修正 free_bytes 查現有目錄、宿主用量測試假設、字幕隔離 Python UTF-8；Windows ZIP POSIX 執行權限與冷啟動版本已修。
- M7 以 C: NVMe 為 SSD 測試根；E: 為 USB 外接磁碟，不能當 SSD 證據。

### LANDMINES

- GitHub CLI 目前 token invalid；本機不能直接推送或觸發原 CI。GitHub Actions macOS runner 可能消耗付費 CI 額度，未取得執行授權。
- macOS Finder、Tk、程序控制與正式 CI 未實際驗證，Stage12/13 不可宣稱完成。
- Stage12 候選與 Stage13 草稿不能混同提交；.idea staged 屬使用者。

### 證據索引

- routinekeel/flow_base/docs/test-report.md AC/M 對照與測試命令
- tests/test_integration_recovery.py、test_integration_atomic_publish.py、test_security_diagnostics.py、bench_run_status.py
- Stage10 commit 16d0fd6；git status 顯示 Stage12/13 WIP 與 .idea staged
- 手動 CI 矩陣 .github/workflows/platform-checks.yml
- gh auth status default token invalid

### VALIDATION

- Windows 完整回歸 — PASS：目前程式碼 382 passed、21 skipped、0 failed，耗時 212.82 秒；包含 v004 缺表、輸入漂移、100 次混合與 MCP。
- WSL Linux 完整回歸 — PASS：OpenClawGateway /tmp 隔離副本建置 Valid YES、390 passed、13 skipped，耗時 94.33 秒。
- M7 SSD 延遲 — PASS：Get-Partition C | Get-Disk 確認 C: 位於 NVMe Micron；5,000 Run／200 次查詢 p95 9.78ms <200ms。
- Stage12 macOS／GitHub CI — NOT_RUN：手動矩陣 .github/workflows/platform-checks.yml 已寫但未推送／觸發；gh auth status 顯示 token invalid。
- Stage13 文件 — NOT_RUN：草稿存在並有相對連結檢查 0 缺失，仍未完成正式發布與使用者走查。

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


## [2026-11-17T07:55+08:00] Stage12 本機整合矩陣通過

<!-- devnote-review:e6cf94e48fa759422a982144 -->

Stage12 新增 Action/Demo 失敗事件、佇列輸入漂移、v004 缺表與 100 次混合 Step 前後事件驗證；Windows 382 passed/21 skipped，WSL Linux 390 passed/13 skipped。C: NVMe 的 5,000 Run p95 9.78ms。正式 GitHub/macOS CI 未執行，Stage12 候選待提交與外部 Gate。
