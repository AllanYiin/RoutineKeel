# 生成與驗證的持久事件

依原規格 §9.1／§10.1，生成與驗證使用既有 `studio_events`，不新增 schema。
事件保存 project_id、revision_id、遞增 sequence，payload 以 build_id／report_id 對應實際成果。
`EventStore.read(project_id, after=sequence)` 可在重新開啟後接續讀取；工作台 model 的
`project_events` 也讀同一份紀錄。這些是本機確定性處理進度，不冒充模型的 token 串流。

| 事件 | 可作出的判斷 |
|---|---|
| generation.started | 此次生成已開始，尚無可用候選包 |
| generation.delta | 已通過契約檢查，或候選檔案已寫入；階段及說明分開保存 |
| generation.completed | 完整性檢查已通過，build 與此事件在同一交易提交；仍只是 candidate |
| generation.failed | 此次生成失敗，保存非機密原因代碼及最後確認階段；不能發布未登錄目錄 |
| verification.started | 已開始一次獨立驗證，識別碼不與前次報告混用 |
| verification.completed | 報告、build 狀態及事件已同交易提交；status 可以是 passed 或 failed |
| approval.required | 同交易保存的報告為 passed，仍需人工核准；事件本身不會建立 approval |
| verification.failed | 驗證程序或報告提交未完成；保存最後靜態檢查階段及已通過案例 ID |

一般例外的原始文字可能含本機路徑，因此失敗事件只保存原因代碼，不複製原始例外內容。
Runtime 的詳細診斷仍留在該次本機 verification 目錄。程序遭強制終止時可能只留下 started，
讀取者不能據此推論完成；本次未新增自動重試或替未登錄成果補寫成功事件。
舊建置與舊報告不補造事件歷史。
approval.required 只表示這份固定報告通過，不保證之後的來源、案例或工具狀態仍符合核准條件；
真正核准與發布仍會重新查核既有政策與版本。

`test_operation_events.py` 先重現缺陷，再驗證重開與 sequence 接續、I/O 失敗、
業務比對失敗不要求核准，以及完成事件寫入失敗時 build／report／狀態整筆回滾。
建置結果讀回也在同一交易內；不能因提交後讀回失敗而同時留下成功與失敗事件。
注入 SQLite 失敗僅作用於測試資料庫；歷史成功成果不受影響。
