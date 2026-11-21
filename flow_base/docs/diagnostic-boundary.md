# 診斷與原始錯誤保護（Stage 5）

`record_exception` 保存原始 Exception 的 `code`、穩定 `error_id`、堆疊與最多 8 層 cause chain。多層 Run／CLI 包裝沿用同一個 `error_id`。`cause_status` 為 `confirmed`、`suspected` 或 `unknown`；只有直接的驗證或 Runtime 檢查能標示 confirmed。子程序非零退出只能確認退出事實，不能據此宣稱 OOM 或模型錯誤為根因。`evidence` 僅保存對應的安全事實。

失敗時依序嘗試本機 `error.json`／`exception.log`、SQLite `diagnostics`，最後才輸出只有 `error_id` 與錯誤碼的 stderr 訊息。`diagnostic_persistence` 會標明 `run_log`、`db_only` 或 `stderr_only`；未寫成的 `diagnostic_json` 或 `exception_log` 為 `null`，不可當作可開啟檔案。SQLite 寫入診斷使用短暫 busy timeout，避免 DB locked 時拖住錯誤回報。

Engine、Action、launcher、CLI 與獨立 supervisor 的失敗收尾均把 Reporter、DB、失敗報告等操作視為次要動作。次要操作失敗時，保留原始例外向上傳遞，另以 `parent_error_id` 記錄 secondary diagnostic；若次要診斷本身也失敗，至少在原始診斷物件中保留操作名稱與失敗類型。成功交付後的呈現錯誤另記錄，不能反轉已成功的 Run 狀態。

分享時使用 `observability.diagnostics.safe_export(record)` 或 `export_diagnostics(record, destination)`。預設輸出只含受控錯誤碼、ID、確定程度、證據種類與診斷持久化狀態；不含原始訊息、路徑、stack、prompt、輸入內容或 token。此功能只寫入呼叫者指定的本機目的地，不自動上傳。未列入公開錯誤碼的值以 `UNCLASSIFIED_ERROR` 呈現。完整本機診斷仍供維護者在授權範圍內查閱。

驗證：`tests/test_diagnostics_fallback.py`、`tests/test_diagnostics_redaction.py`，以及既有 `tests/test_diagnostics.py`。故障注入涵蓋磁碟滿、唯讀、SQLite locked、JSON 序列化、Reporter.fail、DB 狀態更新、多層 cause、CLI fallback 與秘密標記。
