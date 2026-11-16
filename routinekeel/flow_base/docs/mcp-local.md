# 本機 MCP 與持久化佇列（Stage 10）

RoutineKeel 的 MCP 入口只處理短時間的提交、查詢和取消。任務先寫入 SQLite，立即回 `execution_id`；**另外啟動**的本機 `queue-worker` 逐件領取，正式執行時才建立 `run_id`。關閉 Codex、重啟 MCP 或換對話，都可用相同 `execution_id` 查狀態與結果。若 Worker 未啟動，任務會保持 `queued`；提交成功並不代表流程已開始。

## 啟動順序

1. 在發行工作區另開啟 `flow_base/queue_worker.bat`（Windows）、`queue_worker.command`（macOS），或執行 `queue_worker.sh`（Linux）。它委派既有 `run_app` bootstrap，不開 Port。視窗須保持開啟才能領取後續工作；可用 Ctrl+C 停止，佇列資料仍留 SQLite。
2. MCP 是選配套件。發行包先在終端執行一次選配安裝：Windows PowerShell 用 `& .\flow_base\start.ps1 --install-mcp`；macOS／Linux 用 `sh ./flow_base/start.sh --install-mcp`。一般 CLI／Worker 不需要 MCP 依賴。**宿主的 MCP command 不可指向會顯示 bootstrap 文字的 `run_app`／`start.ps1`**；Windows 指向工作區 `.venv\Scripts\python.exe`，macOS／Linux 指向 `.venv/bin/python`，參數依序為 `-I -m flow_base --workspace <工作區根目錄> mcp-stdio`。如此 `stdout` 只供 MCP 訊息，日誌走 stderr 或本機檔案。

   在開發環境執行：

   ```powershell
   uv sync --project routinekeel/flow_base --extra mcp --group dev
   uv run --locked --project routinekeel/flow_base --extra mcp flow_base --workspace <工作區根目錄> mcp-stdio
   ```

   MCP Client 以 stdio 啟動，使用官方 SDK 協商；安裝步驟與正式協定連線分開。
3. 先 `plan_workflow`，再 `submit_workflow`；記下 `execution_id`。同一 `idempotency_key`＋相同有效規格重送會回原 ID，不同規格拒絕。`get_workflow_status` 回 queued／running／terminal、`run_id`（未開跑時為 null）、安全進度及建議輪詢間隔；`get_workflow_result` 回已驗證成果路徑和審閱狀態。`cancel_workflow` 在排隊時立即取消，執行中先回 `cancel_requested`，直到 Run 停止才算確認。

MCP 工具：`list_workflows`、`describe_workflow`、`plan_workflow`、`submit_workflow`、`get_workflow_status`、`get_workflow_result`、`cancel_workflow`。CLI 也有 `submit`、`execution-status`、`execution-result`、`execution-cancel`、`queue-worker`，共用相同 Service。CLI 原有 `run` 仍可同步執行，不受佇列模式影響。

佇列第一版只接受工作區內的輸入與輸出；外部檔案須先明確匯入工作區。MCP 不允許任意 shell、不安裝未授權流程、不傳原始診斷或檔案內容，也不綁定 HTTP Port。`result_status=review_required` 表示處理完成但仍待人工審閱，與 `execution_status=succeeded` 分開。資料庫升級至 v004 前有 SHA-256 驗證的 SQLite 備份；回退舊版底座須先還原與舊版相容的 DB。

MCP 2026-07-28 的協定核心無 Session；RoutineKeel 的持久化來自本機 SQLite，不能單靠協定無狀態推論程序會存活。Worker 必須獨立於 MCP 程序啟動。原生 MCP Tasks 擴充只在目標 Client 確認支援後另行映射，普通工具不依賴它。完整契約見 `specs/mcp-queue-v1.md`。

驗證命令：

```powershell
uv run --locked --project routinekeel/flow_base --extra mcp --group dev python -m pytest tests/test_execution_queue.py tests/test_mcp_stdio.py tests/test_integration_mcp_cli.py -q
```
