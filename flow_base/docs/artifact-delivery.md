# ArtifactSet 與版本交付（Stage 7）

舊工作流維持最後 Step 的單檔案發布與既有 flat-v1／categorized-v1 路徑。新工作流可在 Manifest 與 Workflow 同時宣告相同的 `outputs`（名稱 → terminal Step ID）；每個名稱交付該 Step 的主檔與 `output_artifacts` 宣告的 sidecar。新式 Step 的 `cacheable`、`side_effects`、`retry_policy` 均為選用；未宣告時保留舊 handler 行為。`side_effects=local_write/external` 預設不命中快取，外部副作用不可明示快取。此版本只支援手動或不自動重試，不宣稱自動安全重試。

具宣告的 Step 成功後寫入 `artifact-set.json`，記錄每個必要檔案的相對路徑、大小與 SHA-256；任一 sidecar 遺失或變更都會使整組快取失效。新的快取執行寫入每次 Run 的獨立目錄，不覆寫上一個已驗證 ArtifactSet。SQLite v003 將 Step Attempt 與 cache key 關聯至 ArtifactSet；舊單檔案快取仍按原路徑與 Adapter 驗證。

Workflow 發布先在正式 output 同磁碟的隱藏 sibling `.<job>.deliveries/staging/<run_id>` 建完整成果；既有 root 會複製進 staging，以保留人工修訂防護。Adapter 只看到 staging。驗證全部檔案和交付索引後，staging 以 rename 進入不可變 `versions/<run_id>`。正式 `current.json` 指標以原子檔案替換切換；同一筆 SQLite 交易將 Delivery 設為 published 並將 Run 設為 succeeded。若取消先取得 DB 狀態，指標與舊路徑回滾。

為維持舊流程直接讀 `output/report.json`、`artifacts/` 的方式，底座另以逐檔案原子複製維護根目錄鏡像。跨多檔案鏡像本身不是單一 OS 原子操作；因此切換指標前保留 `transactions/<run_id>` journal 和可 rename 回去的舊檔。一般例外立即回滾，程序強制終止時重開工作區依 lease 與 journal 恢復。`current.json` 與不可變版本是正式真相；外部程式若需要跨多檔案一致快照，應依指標讀版本目錄。失敗 Run 的診斷留在 Run log／staging，不能覆寫前一版正式報告。

`rollback_delivery(..., confirm=True)` 會把選定的舊版重新交付為新的 Run／Delivery，保留所有歷史版本，不重新執行工作流節點；若目前根目錄含人工改動，會要求先另存而不覆蓋。快取與版本 GC 均先用 dry-run 列出候選與位元組，實際清理需明確 `confirm=True`；SQLite 仍引用、正式指標所指及 legacy 快照預設不清理。

驗證位於 `tests/test_artifact_sets.py`、`tests/test_atomic_delivery.py`。其中涵蓋多終點、sidecar 遺失、20 個交付故障點、磁碟與 DB 寫入失敗、取消競爭、程序強制終止後重開、舊 manifest 搬移、人工修訂保護、rollback 與 GC。macOS 的目錄／檔案操作仍待實機驗收。
