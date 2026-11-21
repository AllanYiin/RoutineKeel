# DEVNOTE

## SNAPSHOT — 當前狀態

最後更新：2026-11-21T22:45+08:00
交接狀態：verified

### 目標與授權範圍

統一 GitHub 原始碼、Release 與本機使用的目錄。依使用者最新決定，以 E 槽功能為主，字幕保留 GitHub 0.5.0；Workflow Studio 以宿主對話引導，排除 Tk Windows 桌面版。提交與推送交付分支，維持既有合併／發布 Gate；不執行付費生成或正式工作流。

### 目前進度

原始碼改為根層 flow_base/、workflows/FLOW_*/、blueprints/、workflow_studio/；共享狀態／日誌在 flow_base，.venv 首次啟動建立。整合底座的 ZIP 更新、藍圖首裝、宿主／真人待辦及可選媒體元件；保留 GitHub 字幕 0.5.0。版本為 flow_base 0.2.15、字幕 0.5.0、複製範例 0.2.3、Studio 0.2.0。Studio Tk 程式與專屬測試已移至 E 槽忽略的本機備份，保留無視窗控制器，CLI 預設 guide，另有 projects/status。五 ZIP 已建置；底座／流程 529 passed、28 skipped、1 deselected，Studio 515 passed，封裝／更新 90 passed。交付分支 codex/unified-source-layout，C 工作樹由 main 180a134 建立；E 原 staging 保留，D 安裝只做唯讀核對，未升級或執行。

已提交推送 9d07e1b 並建立 draft PR #13。首輪 CI 五個工作成功，Windows Studio 為 508 passed／6 skipped／1 failed；失敗來自 CRLF 模板再轉一次而產生 CRCRLF 的測試資料。已固定受控模板 Git checkout 為 LF，並以 LF／CRLF 兩種來源實際驗證、執行舊套件；另驗證 CRCRLF 仍被拒絕，未改動或放寬模板信任邏輯。修正專項 5 passed，lint／format 通過。

### 下一步

提交並推送換行修正，核對 PR #13 新 head 的六個三平台驗證工作；未全數成功前不得宣稱三平台通過。之後重建綁定最終提交的候選，更新本機 dist。main 合併與發布仍依既有 Gate。

### DECISIONS

- 以使用者後續明確決定取代本輪先前的 main-only 提案：E 底座功能為主，字幕使用 GitHub 0.5.0，Studio 排除 Tk。
- 目錄改名不改 flow_id/job_id/run_id 或 Python namespace。舊平面工作區仍相容；流程更新依既有識別碼保留安裝資料夾。
- 流程 ZIP 接受新舊結構；隨包的 skills/*.skill 為不執行的資料，納入備份、更新與回退。拒絕其他任意巢狀檔案與越界路徑。
- 保留 SQLite、FileLock、uv 與本機無常駐服務架構；未做資料庫 schema 遷移。回退前保存整組最新 DB、專案、發布快照與成果。
- Studio 對話 guide 是引導資料；宿主使用自己的提問介面與模型並保存實際回答。未答不視為同意，沒有宣稱自動逐層訪談狀態／藍圖匯出已完成。
- 既有核心的版本／證據／驗收／人工核准保持分離。來源與工具回傳是資料，不構成工具、外傳、付費或發布授權。
- 私人 Reel、長文藍圖、設定、模型與成果不加入 Git／ZIP；GitHub 只保留字幕與複製兩個參考流程。
- 保留原始 E staged .idea；本輪僅在乾淨 C 分支提交選定整合。D:/RoutineKeel 的既有資料與已安裝版本未改動。

### LANDMINES

- 三平台 GitHub CI、真人對話體驗與正式付費／生成結果尚未在本輪完成驗收。Studio 先前 Stage0–13 的 Tk 視窗 Gate 為退役版歷史，不能當作新對話功能完成證據。
- Windows 整合測試有 ctranslate2/pkg_resources 棄用警告；測試結束另見第三方 pyreadline3 Console.__del__ 的忽略清理訊息。529 項測試無失敗，原始日誌保留。
- D 安裝仍為 0.2.14+layout1，目錄已符合要求；新版 0.2.15 候選未安裝到 D，不宣稱正式工作流已驗收。
- source／E／C 備份及測試產物只在 dist，不提交。E 舊字幕與 Tk 原始碼已完整備份；不要用 git reset --hard 來回復使用者的既有修改。

### 證據索引

- docs/layout-migration.md
- workflow_studio/docs/host-conversation.md
- tools/release.py
- tools/studio_release.py
- tests/test_source_layout.py
- tests/test_release_layout.py
- E:/PycharmProjects/RoutineKeel/dist/layout-integrated-runtime-tests.xml
- E:/PycharmProjects/RoutineKeel/dist/layout-conversation-studio-tests.xml
- E:/PycharmProjects/RoutineKeel/dist/layout-final-package-tests.xml
- E:/PycharmProjects/RoutineKeel/dist/layout-source-backup/
- E:/PycharmProjects/RoutineKeel/dist/installed-layout-verification.json
- tests/studio/test_template_compatibility.py
- .gitattributes
- https://github.com/AllanYiin/RoutineKeel/pull/13
- https://github.com/AllanYiin/RoutineKeel/actions/runs/44914045009

### VALIDATION

- 整合底座與流程回歸 — PASS：E:/PycharmProjects/RoutineKeel/dist/layout-integrated-runtime-tests.xml：529 passed、28 skipped、1 deselected；使用字幕 asr-cpu 鎖定測試依賴。
- 對話版 Studio 完整回歸 — PASS：E:/PycharmProjects/RoutineKeel/dist/layout-conversation-studio-tests.xml：515 passed，無 Tk 專屬測試。
- 發行結構／附件更新 — PASS：E:/PycharmProjects/RoutineKeel/dist/layout-final-package-tests.xml：90 passed，含新舊 ZIP、技能附件更新與回退。
- Studio lint 與格式 — PASS：ruff check 全部通過；158 files already formatted。
- E 原 staging 與狀態搬移 — PASS：git diff --cached --binary 與 dist/layout-source-backup/index.patch 相同；runtime-before/verification.json 核對移動前後 SQLite bytes。
- D 實際安裝結構 — PASS：E:/PycharmProjects/RoutineKeel/dist/installed-layout-verification.json：三流程位於 workflows/FLOW_*，home/logs 在 flow_base；唯讀檢查，provider_calls=0。
- GitHub main 合併／Release 發布／付費生成 — NOT_RUN：依既有手動 Gate；本輪先交付分支與候選，不修改已發布 Release 或啟動生成。
- 首輪 GitHub 三平台 CI — FAIL：https://github.com/AllanYiin/RoutineKeel/actions/runs/44914045009：五個成功，Windows Studio test_template_compatibility 的錯誤 CRCRLF fixture 失敗；原始 job 136875728407。
- CRLF 修正回歸 — PASS：dist/template-newline-tests.xml：5 passed；包含 LF／CRLF 舊模板 wheel 執行及異常換行拒絕。git check-attr 確認 templates/** 為 text eol=lf；模板驗證程式未修改。

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


## [2026-11-21T22:22+08:00] 統一來源與發行結構，採 E 底座、字幕 0.5.0 與對話 Studio

<!-- devnote-review:31c008ec83cdfd64a262168a -->

依使用者選擇整合 E 底座與 GitHub 字幕 0.5.0，將原始碼搬至與安裝相同的根層結構。排除 Studio Tk 並保留無視窗核心，新增 guide 入口及驗證。更新／回退支援巢狀流程與技能附件，五 ZIP 已建置；529／515／90 項測試通過。舊來源、SQLite、Tk 與 E staging 有備份與核對；D 僅唯讀檢查，未執行付費生成或發布。


## [2026-11-21T22:45+08:00] 修正 Windows checkout 的模板換行驗證

<!-- devnote-review:7de286b9c6e1e5f0c6388108 -->

PR #13 首輪 CI 僅 Windows Studio 失敗：模板為 CRLF 時測試再次替換 LF，生成 CRCRLF。固定模板 checkout 為 LF，改以 LF／CRLF 兩種來源做舊套件實際執行測試，並確認異常 CRCRLF 仍拒絕。專項 5 passed、lint／format 通過；未放寬模板信任邏輯，等待新提交三平台 CI。
