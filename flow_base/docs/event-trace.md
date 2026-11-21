# 本機 Flight Recorder（Stage 4）

每次 Run 的 `share_logs/<flow_id>/<run_id>/flight.jsonl` 以 schema `1.0` 保存結構化事件，包含 UTC 時間、單調時鐘經過時間、Run／Step Attempt／Span／父 Span、程序 ID、事件名稱與受白名單限制的屬性。關鍵操作前先 append、flush、fsync；`flight_checkpoint.json` 指向最後成功寫入的事件。`event_index` 是可重建的加速索引，JSONL 才是證據來源。`Runtime.events(run_id, cursor=..., limit=...)` 依 event ID 分頁；索引寫入中斷時可由 `EventRecorder.rebuild_index()` 修復。

為保留既有 CLI 與流程的事件消費方式，原本的 `events.jsonl` 仍只存舊版 Reporter 事件，首尾的 `workflow_started`／`workflow_finished` 等順序保持不變。新的 Flight Recorder 使用獨立 `flight.jsonl`；`Reporter.references()` 同時提供兩者位置。兩類檔案各用跨程序 FileLock 防止 JSONL 行互相混寫。

Flight Recorder 與既有事件檔的預設上限為每段 1 MB、保留 8 個舊段；`console.log` 為每段 4 MB、保留 4 個舊段。檔案輪替後保留穩定的目前檔名、分段清單與截斷標記。這是單一 Run 的大小上限；跨 Run 的總保留／清除政策仍由後續交付階段處理。Worker 原始輸出大小及程序樹取消由 Stage 6 強化。

Flight Recorder 的屬性只接受預定識別碼、狀態、數量與指紋，未知欄位直接捨棄；已知環境金鑰值在落盤前遮罩。不得傳入 prompt、私人檔案正文、完整命令列或密碼。子程序呼叫只記錄命令指紋及程序身分，不保存完整 argv。診斷資料由獨立的遮罩流程處理。若事件無法在關鍵操作前落盤，操作以 `EVENT_PERSIST_FAILED` 停止；若只失去 SQLite 索引，仍可從 fsync 後的 JSONL 查詢並重建。

驗證：`tests/test_event_trace.py` 涵蓋結構、時間線、敏感標記、索引故障、跨程序寫入、輪替、超時與失敗事件；完整回歸需從 repo 根目錄打包後執行。
