# flow_base 架構與邊界（Stage 13 草稿）

RoutineKeel 是本機工作區程式。`flow_base` 安裝一次，工作流各自封裝 wheel 和 `flow.json`，放在同一個工作區的同層資料夾。底座只讀 manifest 與 Adapter 契約，不包含字幕或其他流程的領域演算法。

```text
Codex / MCP stdio → submit_workflow → SQLite execution_queue
                                          ↑              ↓
                                 execution_id 查詢   另啟 queue-worker
                                                         ↓
CLI / UI 直接執行 → WorkflowService → 按需 supervisor
                                           ↓
                               Engine + RunContext / SQLite
                                           ↓
                         staging → immutable version → current.json
```

`core/service.py` 是 plan、submit/start、status、events、cancel、diagnose、artifacts、export 與 safe retry 的共同入口；`cli.py`、`ui/app.py` 和可選的 `adapters/mcp_stdio.py` 只作參數與呈現轉接。`plan` 在隔離子程序內用唯讀 discovery 和暫存 wheel 載入，不建立使用者的 DB、插件快取或輸出。MCP 的 `submit` 先寫佇列並回 `execution_id`；另行啟動的 queue-worker 才透過 `start` 以規格 hash 和 idempotency key 建立 Run。

工作區的 `share_states/state.sqlite3` 保留舊 `flows/runs/steps/requests` 四表，schema migration v001–v004 增加 revision、Run／Step Attempt、ArtifactSet、Delivery、diagnostic、event index 與持久化任務佇列；升級前建立帶 SHA-256 的 SQLite 一致性備份。`execution_id` 在入列時存在，`run_id` 在正式執行時建立，`flow_id` 識別安裝流程，`job_id` 識別邏輯工作，`error_id` 關聯故障。舊 `runs.status` 原值不在 migration 中改寫；新生命週期由 `run_executions` 表保存。

Run 狀態按 `created → queued → preparing → running → publishing → succeeded` 推進，`failed/cancelled/interrupted` 為終態。`cancelling` 表示已請求停止，並非已停止。SQLite CAS、單工作區寫入認領與 lease 保護狀態；PID 同時核對啟動時間與 token。`RunContext` 讓每次 Run 的 logdir、事件、取消與 token 用量在並發時隔離。`flight.jsonl` 保存因果事件，`events.jsonl` 與 `console.log` 保留舊消費者需要的格式。

Step 成果必須有可驗證 ArtifactSet；正式交付先寫 staging，再發布不可變版本並切換 `current.json`。前一個成功版本保留，可按明確操作回滾。舊 `output/` 鏡像用 journal 恢復；強制終止後到重開工作區前，鏡像可能短暫不一致，`current.json` 指向的 immutable version 才是正式快照。缺檔或 hash 不符的 sidecar 不命中快取。具外部副作用的 Step 不自動安全重試。

診斷分 `confirmed/suspected/unknown`；Error Catalog 提供繁中說明與下一步。原始例外與完整程序輸出只留本機；分享包只輸出 allowlist 欄位並須明確選擇目標。Worker 環境只傳必要 OS／運算變數，底座不把雜湊正確的第三方 wheel 說成可信沙箱。

本機 UI 使用 Tk，無網路服務；MCP 只在 Client 啟動時使用 stdio。佇列 Worker 由使用者或啟動器另外啟動，不屬於 MCP Client 的程序樹；Worker 未啟動時任務保持 queued。詳見 `mcp-local.md` 與 `test-report.md`。此架構圖描述已實作模組，並不表示所有平台驗收已完成。
