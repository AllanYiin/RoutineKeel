# Error Catalog 與離線診斷（Stage 8）

底座對既有 `FlowError.code` 保持原值，`observability/errors.py` 將其明確對應到以下穩定分類。每個已知碼均有繁中標題、說明、至少一個修復動作、重試政策與需要的證據；未知碼只說明證據不足，不猜根因。詳細 alias 清單以同檔 `GROUPS` 為準，測試會對照底座所有靜態 `FlowError` 碼。

| 標準碼 | 使用者看到的問題 | 原則 |
|---|---|---|
| INPUT_NOT_FOUND | 找不到輸入 | 核對檔案、流程或 Run 身分 |
| INVALID_CONFIGURATION | 設定需要修正 | 指出驗證位置後重新計畫 |
| DISK_SPACE_INSUFFICIENT | 可用空間不足 | 保留舊成果，先釋出空間 |
| RESOURCE_INTEGRITY_FAILED | 資源完整性未通過 | 不使用缺失或 hash 不符內容 |
| DEPENDENCY_UNAVAILABLE | 所需環境尚未就緒 | 使用 doctor／verify 找缺項 |
| PROCESS_START_FAILED | 無法啟動工作程序 | 查看啟動證據及權限 |
| WORKER_TIMEOUT | 處理超過時間限制 | 檢查最後進度與資源後由人決定重試 |
| WORKER_CRASHED | 工作程序異常結束 | 非零退出不等於已確認 OOM |
| STATE_CONFLICT | 目前狀態無法執行此操作 | 查 Run 狀態與競爭操作 |
| DELIVERY_FAILED | 成果未能安全交付 | 檢查版本、staging 與指標 |
| PERMISSION_DENIED | 操作尚未獲准 | 確認範圍或路徑權限 |
| UNEXPECTED_INTERNAL_ERROR | 未預期內部錯誤 | 保留 ID 與已遮罩診斷 |
| DIAGNOSTIC_PERSIST_FAILED | 問題紀錄未完整保存 | 記下最低限度 ID，再檢查日誌磁碟 |
| UNKNOWN | 尚無法判定問題 | 不把模型推測當成已確認根因 |

`cause_status=confirmed` 只表示已具直接證據；`suspected` 表示部分證據，`unknown` 表示尚不足。Error Catalog 的「已知碼」不自動把根因標為 confirmed。完整原始訊息、堆疊與程序證據僅留本機；分享匯出沿用 Stage 5 的安全 allowlist。

從工作區使用以下命令；五個命令均不依賴網路或 LLM：

```text
flow_base --workspace <工作區> doctor [--format=json]
flow_base --workspace <工作區> diagnose <run_id> [--format=json]
flow_base --workspace <工作區> logs <run_id> [--tail 20] [--format=json]
flow_base --workspace <工作區> explain-error <error_id 或已知錯誤碼> [--format=json]
flow_base --workspace <工作區> export-diagnostics <run_id> [--format=json]
flow_base --workspace <工作區> export-diagnostics <run_id> --output <新的本機檔案.json> --confirm [--format=json]
```

`doctor` 只讀檢查 Python、uv、工作區權限、SQLite 及資源快取狀態；尚未逐檔驗證的資源會明示 unknown，並請使用對應流程的 `verify`。`diagnose` 同時顯示最後事件與最後已確認操作。`logs` 預設只列檔案與大小，`--tail` 才會顯示已遮罩的 console 尾端。`export-diagnostics` 預設只預覽，明確 `--confirm` 且指定新檔名後才寫入；不覆蓋既有檔案，也不自動上傳。

新診斷命令的退出碼：`0` 查詢成功、`1` 已知故障／doctor 需處理、`2` 識別碼或參數錯誤、`3` 內部診斷錯誤。舊 CLI 指令的退出碼和 JSON envelope 保持相容；`--format=json` 的 stdout 只含一個 JSON envelope。
