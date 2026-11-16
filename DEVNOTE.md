# DEVNOTE

## SNAPSHOT — 當前狀態

最後更新：2026-11-17T05:28+08:00
交接狀態：verified

### 目標與授權範圍

依 RoutineKeel_flow_base_spec_v1.2.2.zip 與 devnote:devloop 完成 Stage 0–13，逐階段驗證並提交。

### 目前進度

Stage 0–9 已各自提交，Stage 9 commit 9ead2a4。Stage 10 MCP stdio 仍有 Windows Client 關閉後 supervisor 中斷的未解問題，未提交。Stage 11 本機 Tk 工作台、雙擊入口、指南已實作並完成適用驗證，準備獨立提交。Stage 12–13 未開始。原有 .idea/ 七個 staged 檔案屬使用者變更。

### 下一步

用獨立 Git index 只提交 Stage 11 檔案及 CLI 的 ui hunk，保留 Stage 10 WIP；接著讀 Stage 12 驗收矩陣並推進不依賴 Stage 10 的測試與平台修正。Windows MCP Client Job Object 問題仍需可靠、合規的程序生命週期方案。

### DECISIONS

- 保留 Python／uv／SQLite／FileLock、Local-first、無常駐 HTTP；不改流程領域演算法。
- Stage 10 採官方 MCP Python SDK 選配，八個 Service 工具；Windows SDK Client 關閉時 KILL_ON_JOB_CLOSE，普通子程序與 CREATE_BREAKAWAY_FROM_JOB 子程序均未存活，不能假稱通過。
- Stage 11 以標準庫 Tk + WorkflowService 實作本機 UI；PNG/GIF 與 UTF-8 文字可預覽，其餘顯示真實檔案 metadata 與不支援原因；開啟、執行、取消、診斷均讀 Service。
- 發行 ZIP 明確保存 POSIX .sh/.command 0755 權限；UI 入口委派既有 run_app bootstrap。

### LANDMINES

- Stage 10 Windows MCP start 可回 run_id 後隨 Client 清理 Job Object 而中斷，尚未驗收與提交。
- 此 sandbox Task Scheduler 不可用；不得藉繞過 Client 安全控制假裝長任務可存活。
- Windows 完整回歸仍有 14 個既有平台／sandbox／編碼失敗；Stage 12 須檢查矩陣。
- macOS Finder 與 Tk 真機操作尚未實測。
- Stage 11 提交須排除 Stage 10 WIP 與使用者 .idea staged。

### 證據索引

- routinekeel/flow_base/src/flow_base/ui/app.py、ui/model.py
- tests/test_ui_contract.py
- tools/release.py 與 dist/release/flow_base-0.2.11.zip
- routinekeel/flow_base/docs/user-guide.md、README.md
- tests/test_mcp_stdio.py 的 Windows Client 關閉失敗

### VALIDATION

- Stage 11 Tk UI 契約與操作 — PASS：tests/test_ui_contract.py 4 passed；含選檔/預覽、按鈕範例執行、歷史、移動副本重開、錯誤卡、取消、診斷匯出與無網路啟動。
- Stage 11 發行包 — PASS：release.py Valid YES，三 ZIP 成功；flow_base ZIP 含 open_ui.bat/.sh/.command、user-guide.md，wheel 含 ui/app.py 與 ui/model.py；POSIX 入口 mode 0755。
- Stage 11 完整 Windows 回歸 — PASS：pytest tests routinekeel/flows --deselect Stage10 client-exit case：361 passed、14 個既有基線失敗、10 skipped、1 deselected；ZIP mode 測試由既有失敗變通過。
- Stage 10 MCP stdio — FAIL：tests/test_mcp_stdio.py 2 passed、1 failed；Windows Client close/reconnect 後 Run 未完成。受控 SDK 子程序 probe 中普通與 CREATE_BREAKAWAY 子程序均未存活。
- Stage 12–13 — NOT_RUN：尚未開始。

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
