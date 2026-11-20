# DEVNOTE

## SNAPSHOT — 當前狀態

最後更新：2026-11-21T01:59+08:00
交接狀態：refined

### 目標與授權範圍

使用者要求 GitHub Release 的輸出採 RoutineKeel/.venv、flow_base/{share_logs,share_states}、workflows/FLOW_*、blueprints、workflow_studio 結構；補充主線為 Studio 產生藍圖包，底座轉換為真實 workflow，GitHub 保留2–3個案例，不持續擴充內建流程。已明確授權「commit push」。本次不合併、不發布 Release、不執行付費模型。

### 目前進度

實作與本機驗證完成，交付分支 codex/release-workspace-layout。來源為 GitHub main 的4c9f457，隔離工作樹 C:/Users/allan/.codex/worktrees/release-workspace-layout/RoutineKeel；E:/PycharmProjects/RoutineKeel 的既有 dirty 工作樹完整保留，沒有拷入0.2.14未提交功能。

預設 tools/release.py 產生5個ZIP：底座、Studio、字幕與複製兩個既有案例、RoutineKeel-workspace完整包。分包沿用同一相對位置；流程在workflows/FLOW_*，底座狀態與紀錄在flow_base內，.venv首次啟動才建立。--project仍只建置指定套件。完整包排除環境、模型、私人設定與成果，保留空blueprints目錄。Runtime、計畫、交付引用、輸出防護、三平台入口與生成指引支援新結構；舊平面工作區仍相容。字幕review-import與recover-run使用工作區根目錄解析相對路徑，沒有修改領域算法。

README與規格改為Studio→藍圖→底座的產品方向，只保留現有兩個案例。這不是把main尚未完成的藍圖轉換功能宣稱完成。Windows完整底座與流程回歸429 passed/26 skipped；最後打包與Studio安裝保護23 passed/1 skipped。五ZIP CRC/SHA256全部通過，乾淨Windows首次準備與UV_OFFLINE=1再次啟動均ready。GitHub三平台CI與遠端交付狀態以本分支實際查詢為準；不沿用本檔舊Stage13或PR3狀態推定本次發布。

### 下一步

依使用者已授權的commit/push交付此分支並建立draft PR；若遠端分支已存在，檢查其最新CI與PR狀態。不要自動合併main或勾選publish_release。GitHub三平台及真人走查尚未在此文件中宣稱通過。

### DECISIONS

- 主包包含Studio與底座，現有課程字幕／檔案複製僅為參考案例；不新增領域流程或擴大案例清單。
- 從最新main另建隔離工作樹，保留其他分支、PR11與E工作樹未提交內容。本次沒有更改已安裝D:/RoutineKeel的正式套件。
- 新結構由workflows目錄判定；舊式平面配置仍相容。根層.venv由啟動器建立，不打包可執行環境或快取。
- 實際發行仍保留workflow_dispatch、main、publish_release及三平台成功Gate。使用者只授權commit/push，不能當作合併或發行授權。
- 先前Stage0–13資料保留在歷史，驗收依當前程式、測試與本次CI，不把舊DEVNOTE的PR3開放狀態當最新事實。

### LANDMINES

- 本地使用main既有版本底座0.2.12、字幕0.4.1、Studio0.1.0；E工作樹未提交0.2.14功能沒有混入。本次解決發行結構，未實作未完成的Studio藍圖轉換能力。
- Windows本機測試已過；26與1項skip包含平台／既有環境限制，不代表macOS/Linux原生GUI、Finder或真人驗收。
- 完整ZIP約3.4MB為乾淨建置候選，不含D工作區的私人Reel、長文藍圖、媒體或來源。dist與運行資料不提交。
- 原E工作樹的dirty內容、.idea與先前其他專案進度不在本次提交範圍。

### 證據索引

- 使用者本輪Release目錄要求、Studio/藍圖/底座與2–3案例方向、commit push授權
- tools/release.py
- .github/workflows/packages.yml
- tests/test_release_layout.py
- routinekeel/flow_base/docs/test-report.md
- D:/RoutineKeel/flow_base/share_states/verification/github-release-layout-summary.json

### VALIDATION

- 完整底座與兩流程回歸 — PASS：pytest tests routinekeel/flows --ignore=tests/studio -q：429 passed,26 skipped；完整log在D:/RoutineKeel/flow_base/share_states/verification/github-release-layout-full-tests.log。
- 最終打包／Studio保護 — PASS：pytest tests/test_release_layout.py tests/studio/test_distribution_package.py tests/studio/test_distribution_bootstrap.py -q：23 passed,1 skipped。
- 乾淨發行ZIP與分包 — PASS：5 ZIP CRC、SHA256SUMS與分包／完整包逐檔相同；真實ZIP內wheel完成catalog、plan、run、cache，輸出防護正例負例通過。
- Windows正式入口 — PASS：乾淨ZIP首次run_app.bat --prepare-only --offline成功，首次下載Python/底座依賴；隨後UV_OFFLINE=1再啟動ready。未下載模型或付費呼叫。
- GitHub CI依賴組合 — PASS：uv run --locked --project routinekeel/flow_base --extra mcp --group dev --with-requirements dist/subtitle-test-requirements.txt 成功。
- GitHub三平台CI／發布 — NOT_RUN：尚需以提交後GitHub的最新分支與PR查詢；未自動合併或發布Release。

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


## [2026-11-21T01:59+08:00] Release採新式工作區、Studio主包與兩個參考案例

<!-- devnote-review:7c712d47fba770a0b1975863 -->

完成新式Runtime路徑與Release結構，預設5ZIP含Studio完整包、保留舊平面相容性與2個案例。修正字幕審閱與恢復的工作區相對路徑，新增真實解壓包測試。Windows底座與流程429pass/26skip，最終打包23pass/1skip，5ZIPCRC與SHA正常，乾淨首啟動及離線再啟動ready。使用者授權commit/push，使用隔離分支交付，未處理其他dirty工作或自動發布。
