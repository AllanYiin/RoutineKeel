# DEVNOTE

## SNAPSHOT — 當前狀態

最後更新：2026-11-17T08:35+08:00
交接狀態：verified

### 目標與授權範圍

依 RoutineKeel 底座階段規劃及使用者核定的 MCP 佇列修訂完成 Stage 0–13、逐階段驗收並交付。

### 目前進度

Stage 0–12 的程式與整合 Gate 已提交並在 GitHub draft PR #3 的 Ubuntu/Windows/macOS CI 通過；Stage10 16d0fd6，Stage11 ad753e0，Stage12 候選 a0495ba，後續跨平台修正 d08c5c4/24354da。Stage13 文件候選 f69402f；最後 CI 結果與真人走查清單正在更新，PR 保持 draft、未合併未發布。原有 .idea/ 七個 staged 檔案屬使用者變更。

### 下一步

將 CI run #9 的三平台結果與未覆蓋 Mac Finder/Tk 真人走查寫入報告/手冊，提交並推送 doc-only 更新，再追蹤最後文件變更的 CI 終態。等待使用者對未接觸程式碼的操作走查回覆；未取得人工證據與明確合併/發布授權前不宣稱 Stage13 發行完成。

### DECISIONS

- Stage10 MCP 以 execution_id/SQLite v004/獨立 queue-worker 執行，MCP Client 無狀態查詢且不派生 Worker。
- PR #3 使用 Ubuntu build 加 Windows2025/macOS15 Gate；publish 需三平台成功，draft PR 不合併/不發布。
- Windows cp1252 與 macOS 缺 process_started_at 已依第一次/第二次 CI 日誌修正；發行啟動器和 CI 明確用 UTF-8。
- Stage12 指標依三平台自動測試與 C: NVMe p95 9.78ms 驗收；Mac GUI/Finder headless 未覆蓋須列明。

### LANDMINES

- Stage13 手冊規定未接觸程式碼的人走一次安裝/範例/診斷/恢復，尚待使用者安排；macOS Finder/Tk 互動桌面不由 CI 證明。
- 實際舊版 wheel 回退未演練，只有 SQLite 備份 checksum/restore 測試；發布前需決策。
- 最後文件更新尚未跑其對應 head commit 的完整 CI；PR 仍 draft。
- .idea staged 屬使用者，所有提交與推送均排除。

### 證據索引

- draft PR https://github.com/AllanYiin/RoutineKeel/pull/3
- GitHub Actions run 43987170358 各 job logs
- routinekeel/flow_base/docs/test-report.md 與 release-checklist.md
- routinekeel/flow_base/docs/user-guide.md 新增走查清單
- git status：.idea 七個 staged 使用者檔案與待提交 docs

### VALIDATION

- GitHub PR CI run #9 — PASS：https://github.com/AllanYiin/RoutineKeel/actions/runs/43987170358 commit 24354dac：Ubuntu 401 passed/3 skipped、macOS15 402 passed/2 skipped、Windows2025 383 passed/21 skipped；三 job success、publish skipped；Ubuntu三 ZIP SHA256SUMS OK。
- 本機 Windows/WSL — PASS：Windows 382 passed/21 skipped，WSL Linux 390 passed/13 skipped；M7 C:NVMe 5,000 Run p95 9.78ms。
- Stage13 文件候選 — PASS：source.zip 含架構/known issues/release checklist/upgrade/合成診斷，三 ZIP SHA正確，文件連結0缺失；user-guide 新增真人走查步驟，後續需重建驗證。
- Mac Finder/Tk 真人走查與舊 wheel rollback — NOT_RUN：headless CI不具互動視窗；使用者走查回覆待處理；舊 wheel回退未演練。

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


## [2026-11-17T08:06+08:00] Stage13 文件候選與外部驗收待授權

<!-- devnote-review:4aaf8eeedfe4317ddd7163d4 -->

Stage13 文件候選 f69402f 已提交；本機三 ZIP SHA、source 文件、連結與 Worker 冷啟動通過。Windows 382 passed/21 skipped、WSL Linux 390 passed/13 skipped。正式 Ubuntu/Windows/macOS CI 與真人走查未完成，等待使用者對可能付費的 CI 推送/執行授權。


## [2026-11-17T08:35+08:00] 三平台 PR CI 通過，Stage13 人工 Gate 待驗

<!-- devnote-review:c76f4a3b22a97acd2afdc5ce -->

PR #3 第三輪 CI Ubuntu401/3skip、macOS402/2skip、Windows383/21skip 均成功；前兩輪揭露並修復 Windows cp1252 和 macOS 程序出生時間。Stage12 整合 Gate 已具三平台證據。Stage13 更新測試報告與真人走查步驟，仍待最後 doc-only CI、Mac GUI真人走查及發布授權。
