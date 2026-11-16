# RoutineKeel 開發 handoff

## SNAPSHOT

- 目標：依 `C:\Users\allan\Downloads\RoutineKeel_flow_base_spec_v1.2.2.zip` 的 Stage 0–13 與 `devnote:devloop` 逐階段實作；Stage 0–3 已提交，Stage 4 已驗證待提交，Stage 5 尚未開始。
- 基準 HEAD `4f7ecf25`；Stage 0 commit `490ed4d`、Stage 1 `21fec1f`、Stage 2 `7c73f2f`、Stage 3 `b4d3179`。
- 工作樹原有 `.idea/` 七個暫存檔案屬使用者變更，勿修改或納入後續提交。

## DECISIONS

- 保留 Python／uv／SQLite／FileLock、Local-first、無常駐 HTTP；底座不修改字幕領域邏輯。
- Stage 1 保留 legacy SQLite 四表，新增 v001 schema 與一致性備份；舊 runs 透過原 facade 讀寫並以 view 顯示推定狀態，原值仍保留。
- Stage 2 使用 `RunContext`/`ContextVar`，移除核心路徑對共享 `run_logdir` 的依賴；`action_context` 保留相容屬性。
- Stage 3 新增 v002、Run／Step Attempt CAS 狀態機、單工作區寫入認領、idempotency、heartbeat／lease、程序身分辨識與重開 reconcile；現有 Workflow／Demo／Action 已寫入新狀態表。另有按需啟動的獨立 `start_detached` supervisor，Client 退出後可接管保留的 run_id；既有同步 CLI 保持可用。
- Stage 4 保留 Reporter 的 `events.jsonl` 首尾相容順序；新增 `flight.jsonl` 結構化事件、span/parent 與程序身分、fsync 與 checkpoint、索引及 cursor 查詢、輪替／截斷標記。Worker、資源、節點、交付與診斷持久化失敗均有事件；詳見 `routinekeel/flow_base/docs/event-trace.md`。

## VALIDATION

- 原始 HEAD Windows 全測：`197 passed, 15 failed, 10 skipped`，失敗主要因缺少 `sh`、Windows 路徑／ZIP mode、字幕隔離程序編碼等；詳見 `routinekeel/flow_base/docs/upgrade-baseline.md`。
- Stage 1 封裝成功，完整回歸 `205 passed, 15 failed, 10 skipped`；Stage 2 封裝成功，完整回歸 `208 passed, 15 failed, 10 skipped`。Stage 0–2 新增測試 `11 passed`。
- Stage 3：封裝成功，完整回歸 `225 passed, 15 failed, 10 skipped`，15 項與原始失敗清單相同；新增測試涵蓋 100 次 CAS／claim 競爭、雙程序認領、程序身分與 kill/reopen、取消、v001→v002 升級、快取 Attempt 及獨立 supervisor 在 Client 退出後完成。
- Stage 4：封裝成功，完整回歸 `239 passed, 15 failed, 10 skipped`，仍是同一 15 項原始失敗；`tests/test_event_trace.py` 14 passed，相鄰測試組合 54 passed。曾發現舊 Action 要求 `events.jsonl` 首尾固定，已改為獨立 `flight.jsonl` 修正。
- Windows 預設 Python 3.10 不符合專案要求。用 `UV_CACHE_DIR=E:\PycharmProjects\RoutineKeel\.uv-cache` 與 `uv run --locked --python 3.12 --project routinekeel/flow_base --group dev python tools/release.py` 打包；測試可用 `routinekeel/flow_base/.venv/Scripts/python.exe -m pytest`。

## LANDMINES

- `git` 寫 `.git` 需 sandbox escalation；每階段用 `git commit --only -- <明確檔案>`，避免帶入 `.idea/`。
- 原規格 ZIP 未放工作區，從本機路徑用 Python `zipfile` 讀取。原對話的 sandbox 連結在此環境不可直接讀。
- 本機 Windows 全測既有的 15 項失敗不是 Stage 3 引入，包含 POSIX `sh` 測試在 Windows 路徑、ZIP mode 與字幕隔離程序編碼；Stage 12 須處理或正式區分平台測試矩陣。Windows Worker 子程序樹的完整停止仍待 Stage 6 強化。
- Stage 5 下一步：按規格第 10.2–10.3 節建立 cause status（confirmed/suspected/unknown）、例外鏈與 fail-safe 診斷。需檢查 `record_exception`、Engine／Action／CLI 的所有診斷寫入與二次錯誤，確保不覆蓋原始 Exception；補故障注入與秘密遮罩測試。

## HISTORY

- 2026-11-17：完成 Stage 0–2；Stage 3 開始。
- 2026-11-17：Stage 3 實作與驗證完成，含 Client 退出後獨立 supervisor；準備進入 Stage 4。
- 2026-11-17：Stage 4 實作與驗證完成，準備進入 Stage 5。
