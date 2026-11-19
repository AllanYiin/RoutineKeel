# 流程 ZIP 附件更新

底座 0.2.13 提供 `WorkflowService`、CLI 及 MCP 的同一套更新機制。使用者在宿主對話附上流程發行 ZIP 並要求更新後，宿主取得附件本機路徑，完成預覽與套用；不需使用者手動解壓、刪除或覆蓋檔案。

## 入口與授權

Windows 由宿主依已確認的路徑執行：

```powershell
& $Launcher update-workflow --archive $Attachment
# 核對流程與版本，將 result.plan_hash 原值帶入下一步。
& $Launcher update-workflow --archive $Attachment --execute --plan-hash $PlanHash
```

macOS／Linux 使用 `sh "$workspace/flow_base/start.sh"` 搭配相同參數。開發環境使用 `uv run --locked --project routinekeel/flow_base flow_base --workspace <workspace>`。省略 `--execute` 只讀預覽，不初始化 SQLite、不載入附件程式。`--task` 可限定原流程名稱／識別碼，不能藉此將其他流程包覆蓋到目標。附件外層資料夾可不同，實際安裝位置維持原資料夾。

Service：`preview_workflow_update(archive, task=None)` → `update_workflow(archive, plan_hash=..., task=None)`。外部附件須由呼叫方在 `authorized_external_paths` 明確列出；CLI 的 `--archive` 即授權這一條路徑。MCP 工具為 `preview_workflow_update`、`update_workflow`，只讀工作區內的附件；宿主可將使用者指定的附件放入 `.attachments/`，不掃描其他來源、不自行下載新版。

使用者已要求更新時，預覽只是宿主核對步驟，不要求再同意一次。套用綁定附件 SHA-256、目標資料夾、識別碼及既有套件檔案雜湊；任一內容變動須重新預覽。相同版本且內容相同回 `already_current`，相同版本不同內容及降版拒絕；回退須使用更新紀錄。沒有對應的既有流程則拒絕，不隱式安裝。

## 保護與交易

- 接受發行工具或 Studio 的單層流程 ZIP：`flow.json`、唯一 wheel、文件、啟動器、來源包與編譯證據。ZIP／展開總量上限 128 MiB／256 項；wheel 上限 32 MiB／3000 項。拒絕穿越、絕對路徑、重複／大小寫衝突、符號連結、加密檔案及不可攜檔名；目標或備份路徑含連結／junction 時拒絕。
- 驗證 Manifest、Runtime API、流程識別碼、module、wheel SHA-256、namespace 與 ZIP CRC。更新流程不 import wheel、不執行啟動器、不 prepare 模型、不測跑算法。SHA-256 證明完整性，並非發行者簽章；附件來源由使用者選定。
- 套件變更先取得共用 FileLock。Service 的執行、環境準備與入列也共用此鎖；已存在的非終態 Run／排隊工作會阻擋更新，獨立 Worker 可保持待機。已取消的工作不阻擋。`cancelling` 尚未停止，不能切換版本。
- 只寫 ZIP 宣告的套件檔；`settings.json`、`outputs/`、環境、模型、SQLite、執行日誌及其他本機檔案不進替換清單。未被新包宣告的既有檔案保留，包含舊 wheel。flow_id／job_id／run_id 與舊成果不重建。
- 先在 `share_states/workflow_updates/<update_id>/before/` 與 `after/` 保存本次套件快照及 SHA-256，驗證後寫 `record.json` 和 `pending.json`。逐檔原子替換，`flow.json` 最後切換；更新後重讀雜湊，再記錄 `applied` 並清除 pending。POSIX 啟動器恢復執行權限。沒有資料庫 schema 遷移，也不改領域設定格式。
- 一般寫入失敗會嘗試復原。被強制終止時 pending 保留，底座拒絕載入流程；宿主執行復原後再試。套件鎖和 pending 保護適用於同一新版底座，不允許混用舊版執行程序或外部工具同時改套件。並未宣稱能承受任意硬體毀損或實體斷電。

## 復原與回退

```powershell
& $Launcher recover-workflow-update
& $Launcher rollback-workflow-update $UpdateId
```

MCP 對應 `recover_workflow_update()`、`rollback_workflow_update(update_id)`。復原使用本機快照，不需要原始 ZIP。未完成的交易一律回到更新前；即使新版 manifest 已寫入，只要 pending 還存在，仍先復原再重新預覽。重試 `update_workflow` 也會先復原 pending，之後重新核對 plan_hash。

回退先完整驗證快照、目標路徑與目前每個受管檔案。發現後續套件修改或備份損壞就拒絕，不覆寫未知內容。回復本身同樣有 pending／restoring 紀錄，可中斷重試。只移除本次新增且雜湊符合的套件檔，原設定及成果保留；保留 before／after／record 作查核。這是套件回退，不是 SQLite 或業務資料回滾。新版流程若自行改寫領域資料，依該流程的遷移契約處理。

成功回傳 `status=updated`、from_version、to_version、workflow_id、flow_id、folder、update_id、backup_path 和 restart_required。回退回 `rolled_back`。更新不是工作執行，不產生 Run，不宣稱新版算法已驗收；需另行走原本 verify／run。長存程序若已載入舊模組須重開，以免 Python module cache 混版。

## 驗證

```powershell
uv run --locked --project routinekeel/flow_base --extra mcp --group dev python -m pytest tests/test_workflow_updates.py tests/test_workflow_service.py tests/test_execution_queue.py tests/test_mcp_stdio.py -q
```

涵蓋識別與版本、篡改／越界／私人檔案拒收、設定與成果保留、歷史與快取保留、CLI／官方 MCP Client、真正流程更新後執行與回退、一般寫入失敗，以及在套件／manifest／收據寫入後用 `os._exit` 終止測試子程序再復原。這些程序中止測試不代表實體斷電測試。
