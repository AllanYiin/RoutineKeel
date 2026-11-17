# DEVNOTE

## SNAPSHOT — 當前狀態

最後更新：2026-11-17T08:01+08:00
交接狀態：verified

### 目標與授權範圍

依 RoutineKeel 底座階段規劃及使用者核定的 MCP 持久化佇列修訂，完成 Stage 0–13 與驗證提交。

### 目前進度

Stage 0–11 已提交（Stage10 16d0fd6、Stage11 ad753e0）。Stage12 本機整合候選已提交 a0495ba，Windows 382 passed／21 skipped，WSL Linux 390 passed／13 skipped；正式 GitHub Actions 與 macOS 未執行，Stage12 DoD 尚未完成。Stage13 README／架構／回滾／已知問題／release checklist 等文件草稿仍在工作樹，未提交或發布。原有 .idea/ 七個 staged 檔案屬使用者變更。

### 下一步

等待使用者對推送 codex/ 分支與消耗 GitHub Actions 額度執行 Ubuntu/Windows/macOS CI 的答覆；若授權，先使候選與手動矩陣能在目標分支觸發，再追蹤完整結果並修缺陷。其後完成 Stage12 DoD，驗證 Stage13 最終文件/冷啟動/回滾與版本 Gate，再依授權處理發行；未授權前不推送或執行付費 CI。

### DECISIONS

- MCP stdio 只驗證/入列/用 execution_id 查詢與取消；獨立 queue-worker 執行，v004 佇列已提交。
- Stage12 本機候選含 100 次混合 Run/Action、Action/Demo Step Attempt 前後事件、20 交付故障點、四類秘密遮罩、v004 缺表/輸入漂移、平台 sh skip 與手動矩陣。
- Stage13 文件在 source.zip；根 README 面向非技術使用者，發行前要保留未驗限制，不把草稿當正式發行。

### LANDMINES

- GitHub CLI token invalid；GitHub 連接器可讀私有 RoutineKeel 並有 push 權限，但目前無 workflow_dispatch 工具；外部 CI 需要使用者授權與可行觸發路徑。
- 平台矩陣為 workflow_dispatch-only，GitHub 可能要求 workflow 已在 default branch 才可手動觸發；若要 PR 前驗收需調整既有 PR workflow，需注意 CI 額度。
- macOS Finder、Tk、程序控制和實際舊 wheel 回滾未驗；非技術使用者真人走查未做。
- .idea staged 屬使用者，不得納入後續提交。

### 證據索引

- git log -1 a0495ba 與 Stage10/11 commits
- routinekeel/flow_base/docs/test-report.md、本機測試輸出與 bench_run_status.py
- routinekeel/flow_base/docs/release-checklist.md 草稿
- dist/release/flow_base-0.2.12.zip 的 SHA256SUMS/入口/文件檢查
- git status 顯示 Stage13 草稿與 .idea staged

### VALIDATION

- Stage12 Windows 完整回歸 — PASS：382 passed、21 skipped、0 failed，212.82 秒；Stage12 候選提交 a0495ba。
- Stage12 WSL Linux 完整回歸 — PASS：OpenClawGateway /tmp 隔離副本建置 Valid YES、390 passed、13 skipped，94.33 秒。
- Stage13 發行包校驗 — PASS：0.2.12 三 ZIP 的 SHA256SUMS 全數正確；source.zip 包含架構/已知問題/回滾/檢核草稿；乾淨 0.2.12 queue_worker.bat --once 無 MCP 選配 exit0。
- Stage12 正式 CI/macOS — NOT_RUN：尚未推送或觸發；GitHub CLI 認證失效，使用者 CI 授權待答。
- Stage13 最終 Gate — NOT_RUN：文件 link check 0 缺失、合成 example JSON 有效；真人走查、舊版 wheel rollback 與最終 CI/發布未執行。

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


## [2026-11-17T08:01+08:00] Stage12 本機候選提交與發行包走查

<!-- devnote-review:a3cbda4e73ba5a53da111448 -->

Stage12 本機候選 a0495ba 已提交但正式 CI/macOS 仍待；Windows 382 passed/21 skipped，WSL Linux 390 passed/13 skipped。Stage13 文件 link 0 缺失，三 ZIP SHA 正確；乾淨 queue_worker.bat --once 在未裝 MCP 時啟動成功。GitHub 外部 CI 授權待使用者答覆。
