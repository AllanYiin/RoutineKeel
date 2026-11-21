# Stage 10 修訂：本機 MCP 持久化佇列

## 技術規格文件

- **通訊與執行分離**：MCP stdio Adapter 只做 `list_workflows`、`describe_workflow`、`plan_workflow`、`submit_workflow`、`get_workflow_status`、`get_workflow_result`、`cancel_workflow`。MCP 不直接建立或持有 Worker 子程序，不綁定 Port。使用者從另一個終端／啟動器啟動 `queue-worker`；該程序與 MCP Client 的程序樹無父子關係。
- **識別與時序**：提交在 SQLite 交易中建立 `execution_id` 與 `queued`。`run_id` 為 nullable，只在 Worker 實際開始時建立並關聯。`job_id` 與流程既有規格由底座建立，不能與 `execution_id` 混用。重複 `idempotency_key`＋同一有效規格回同一 `execution_id`；規格不同回 `IDEMPOTENCY_CONFLICT`。
- **狀態**：`queued → dispatching → running → succeeded | failed | interrupted | cancelled`；`queued → cancelled`；`dispatching/running → cancel_requested → cancelled | interrupted | succeeded`。`cancel_requested` 不是已停止。成功終態仍須 Stage 7 的可驗證 Delivery；`result_status=review_required` 與 `execution_status=succeeded` 分開顯示。狀態查詢可用已保存 `run_id` 讀實際 Run 終態，即使佇列 Worker 暫時停止也不假稱進行中。
- **認領與恢復**：`execution_queue` v004 有穩定 ID、原輸入／輸出、設定 JSON、offline、spec hash、狀態、nullable run_id、提交／開始／結束時間、worker token 與 lease。每個工作區只允許一個持有 FileLock 的 queue-worker。重新啟動後領回 dispatching／running；Run 建立使用 `execution:<execution_id>` 作 idempotency key，避免在「Run 已建立但佇列尚未記錄」時重複執行。
- **路徑與資訊邊界**：MCP Core 僅接受已安裝流程與工作區內實體輸入／輸出；不信任 Client 任意絕對路徑或 shell。`get_workflow_result` 回傳經驗證的成果 metadata 與路徑，不直接傳文件內容；錯誤訊息遵守 Error Catalog 的已遮罩內容。輸入／輸出在 Worker 啟動時重新 plan 與比對 hash，已改變則拒絕執行。
- **可用性界線**：未另行啟動 Worker 時提交仍成功並保持 queued；UI／CLI 必須提示使用者啟動 Worker。Worker 停止時不刪除佇列。沒有部署 OS 常駐服務，不保證電腦關機後執行；重開 Worker 後可查並繼續可安全處理的工作。

## 非技術規格文件

先開啟 RoutineKeel 的「佇列工作員」視窗。Codex 提交一個工作後，會立刻得到一組 `execution_id`；這代表工作已記錄，不代表字幕已完成。關掉 Codex 或換對話後，拿同一組 ID 查進度即可。工作員會逐件處理，流程自己的進度仍寫在原本的工作紀錄中。完成後可用 ID 查成果位置；若顯示待人工聽審，就照流程指引校對。按取消後，畫面會區分「已請求」與「已停止」。若工作員沒有開啟，狀態會保持「排隊中」，不會假裝背景正在跑。

## Codex／Claude Code 分階段開發計畫

1. **資料與 Core**：新增 v004 佇列表與一致性備份；Service 提供提交、狀態、結果、取消，驗證 idempotency、路徑、規格漂移和 queued 取消。DoD：入列先有 execution_id、尚無 run_id；資料庫重開仍可查。
2. **獨立 Worker**：新增可由原啟動器另外開啟的單工作區 queue-worker，依 SQLite 認領並用既有 supervisor 執行，保持單件處理及崩潰後不重複。DoD：MCP Client 關閉後，Worker 仍可完成並以同 ID 查結果。
3. **MCP／CLI**：MCP stdio 工具改用 execution_id；CLI 提供同契約與 Worker 入口。DoD：官方 SDK 新版／legacy 協商、重連查詢、重複提交、排隊／執行取消、秘密遮罩與無 Port 測試通過。
4. **整合與文件**：對原 CLI／manifest／SQLite 做完整回歸；發行包含 Worker 啟動器、遷移／回滾與使用說明。DoD：Windows／Linux 可重現建置與端到端測試，macOS 及 CI 未跑則如實標示，不把未知標成通過。

回滾：停止 queue-worker 與 MCP，新舊 CLI 仍可直接執行流程；v004 DB 不能由只支援 v003 的舊程式直接寫入，須先驗證並還原 `state.sqlite3.before-v004.sqlite3` 備份。MCP 原生 Tasks 映射不在本階段承諾範圍。
