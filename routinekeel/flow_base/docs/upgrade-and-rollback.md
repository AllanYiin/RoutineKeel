# 升級、備份與回滾（Stage 13 草稿）

適用於 `flow_base 0.2.12` 的目前實作；實際發行前須再以發行 ZIP 演練。工作區由 `flow_base/`、各流程包、`share_states/`、`share_logs/` 與各流程 `outputs/` 組成。更換程式時保留後三類使用者資料。

## 升級前

1. 等所有 Run 達 `succeeded`、`failed`、`cancelled` 或 `interrupted`。`cancelling` 尚非停止。記下工作區位置、底座／流程版本及最近一次成功 `run_id`。
2. 備份整個 `share_states/`、`share_logs/` 與需保留的 `outputs/`，保存到工作區外明確選定的位置。SQLite 在 WAL 模式下不可只複製 `state.sqlite3` 主檔作一致性備份；須先停止寫入，或用 SQLite backup API。
3. 保留原 `flow_base` ZIP 和各流程 ZIP，核對新 ZIP 的 `SHA256SUMS`。若流程包同時更新，先保存原 `flow.json`／wheel 與私人 `settings.json`。

## 安裝與驗證

1. 將新 `flow_base` ZIP 解壓到原工作區的同名 `flow_base/`，保留 `share_states`、`share_logs`、流程資料夾、各流程 `outputs` 與私人設定。啟動器按 wheel 雜湊更新工作區 `.venv`，不需手動刪除原環境。
2. 啟動時 `Runtime.initialize()` 在 lock 下檢查 `PRAGMA user_version` 與 migration checksum；升級 v001、v002、v003、v004 前自動建立 `state.sqlite3.before-vNNN.sqlite3` 與 `.sha256`。重複啟動同一 migration 應為 no-op。未知較新 schema 或不相容舊表會拒絕寫入，保留原檔。
3. 執行 `flow_base --workspace <工作區> doctor --format=json`，再對所需流程執行 `verify`。檢查既有 `history`、最近成功成果與 `deliveries/current.json` 指向的 immutable version。用檔案複製範例完成一次新 Run，再看 `diagnose <run_id>` 與成果。

## 回滾

若新程式失敗，先停止所有 RoutineKeel 程序並保留失敗的 `share_logs`／診斷。舊版底座若不認得新的 `PRAGMA user_version`，**不可直接寫新版 DB**。必須選擇**與目標舊版相容**的升級前備份，先比對同名 `.sha256`，再用 SQLite backup API 還原到 `share_states/state.sqlite3`。例如退回 v003 能讀的底座可選 `before-v004`；退回原始基準 commit `4f7ecf25` 的舊四表實作則須選 `before-v001`，不能選 `before-v004`。完整命令與測試見 `migration-strategy.md`；還原前另存現有新版 DB 供診斷。完成後才替換舊 `flow_base` wheel／ZIP；流程包也須回到相容版本。

回滾資料庫會失去備份時點之後的**執行索引**；已發布成果與日誌不可直接刪除，須人工比對各版本與 `run_id`。若只是新工作結果錯誤，優先使用 Stage 7 的 Delivery 版本回滾，而非回退整個 SQLite。`current.json` 是正式指標；舊根路徑鏡像可由 journal 在重開時恢復。

## 相容界線

| 對象 | 現有承諾 |
|---|---|
| 舊四張 SQLite 表 | v001–v004 保留原值；舊 `runs.status` 透過 view 顯示推定狀態，不能將舊成功視為新交付驗證。v004 不會將舊 Run 自動變成佇列工作。 |
| workflow `flow.json` | 舊 schema 與未宣告具名 terminal output 的流程保持 legacy 單一輸出相容；新流程可宣告多輸出與 Step 副作用。 |
| CLI／MCP | 舊 CLI JSON envelope、命令與退出碼保留；本機 `ui` 可選。0.2.12 的 MCP 佇列與獨立 Worker 已在 Windows／WSL Linux 驗證，macOS／正式 CI 尚未驗收。 |
| 工作區搬移 | 保留整個 `share_states`、流程與成果；`.venv`／隔離環境依新位置重建。正式 Delivery 的歷史索引可從移動後工作區查詢。 |
| 跨 OS | 不複製 Windows/macOS/Linux 的二進位環境；保留資料與流程包，於新 OS 重建執行環境。 |

第三方 wheel 的 SHA-256 驗證只證明檔案未變，不代表安全沙箱。分享診斷需明確預覽與匯出，RoutineKeel 不自動上傳。

### 版本與資料庫相容矩陣

| 底座 | 可寫入的已驗證狀態 | 回退限制 |
|---|---|---|
| 0.2.12 | v004；保留舊四表、既有 Run，新增 `execution_queue`。可與字幕流程 0.3.4、複製範例 0.2.2 的目前測試包協作。 | 回退舊版前先停止 queue-worker，核對 `before-v004` 備份及 checksum。 |
| 原始基準 commit `4f7ecf25` | 舊四表、`PRAGMA user_version=0`；以該 commit 原始碼讀取還原的 `before-v001` 備份，可查到原 Run。 | 不可使用 `before-v004`；先還原 `before-v001`。此測試不是舊 wheel 安裝驗證。 |
| 本機舊發行包 `flow_base-0.2.9.zip` | 舊四表、`PRAGMA user_version=0`；在一次性工作區還原 `before-v001`，其 wheel 由舊 `start.ps1` 在 PowerShell 7 安裝後，`history` 可讀 `run_old`。 | 該舊包的 `run_app.bat` 在此主機呼叫 Windows PowerShell 5.1 時缺少 `Get-FileHash` 而無法啟動；用 PowerShell 7 直接執行舊 `start.ps1` 是本次驗證路徑，不保證其他舊包或電腦。 |
| 其他早於 0.2.12 的版本 | 依該版本實際 schema；本報告沒有對每個舊 wheel 做新版資料庫寫入驗證。 | 不可讓舊版直接寫 v004；先找出相容備份並比對可能失去的新增 Run／佇列索引。 |

上述矩陣只描述本輪實際測試與安全界線，不保證所有歷史流程包版本可混搭。
