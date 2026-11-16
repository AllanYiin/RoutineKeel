# DEVNOTE

## SNAPSHOT — 當前狀態

最後更新：2026-11-17T04:55+08:00
交接狀態：verified

### 目標與授權範圍

依 RoutineKeel_flow_base_spec_v1.2.2.zip 與 devnote:devloop 完成 Stage 0–13，逐階段驗證並提交。

### 目前進度

Stage 0–8 已各自提交；Stage 9 已完成實作與驗證，準備獨立提交；Stage 10–13 未開始。原有 .idea/ 七個 staged 檔案屬使用者變更，勿納入提交。

### 下一步

提交 Stage 9 明確檔案（排除 .idea/）；讀取 Stage 10 技術規格並實作可選 MCP stdio。

### DECISIONS

- 保留 Python／uv／SQLite／FileLock、Local-first、無常駐 HTTP；不改流程領域演算法。
- Stage 1–8 的資料遷移、RunContext、Run 狀態機、事件、診斷、程序控制、原子交付與 Error Catalog 契約延續。
- Stage 9 plan 使用唯讀 discovery、獨立子程序與暫存插件載入；start 由獨立 supervisor 接手；外部路徑需 caller 明確授權；安全重試比對原始快照與規格。

### LANDMINES

- Windows 全測仍有原始基線的 15 項失敗；Stage 12 須處理平台矩陣。
- Stage 7 舊 output 鏡像在強制終止後至工作區重開前可能短暫混合，正式快照以 current.json/immutable version 為準。
- macOS 程序控制、同磁碟 rename 尚未實機驗收。
- git 寫入 .git 需 sandbox escalation；提交時明列路徑排除使用者 .idea/。

### 證據索引

- tests/test_workflow_service.py
- routinekeel/flow_base/src/flow_base/core/service.py、types.py、plan_probe.py
- git diff --check 無 whitespace error
- C:\Users\allan\Downloads\RoutineKeel_flow_base_spec_v1.2.2.zip Stage 9 原文

### VALIDATION

- Stage 9 專項與相鄰測試 — PASS：tests/test_workflow_service.py、test_flow_entry.py、test_recovery_entry.py 對照測試；Stage 9 新測試全數通過，只有既有 Windows sh 測試失敗。
- 發行包建置與內容 — PASS：uv run --locked --python 3.12 --project routinekeel/flow_base --group dev python tools/release.py；Valid YES，三 ZIP 成功；flow_base wheel 包含 core/service.py、types.py、plan_probe.py。
- 完整 Windows 回歸 — PASS：pytest tests routinekeel/flows -q --tb=no --disable-warnings：354 passed、15 failed、10 skipped；失敗清單為 Stage 8 既有基線，沒有新增失敗。
- Stage 10–13 — NOT_RUN：Stage 9 提交後開始。

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
