# Studio 儲存與遷移策略

- 使用獨立 `share_states/workflow_studio/studio.sqlite3`，不寫 flow_base DB。
- Stage 0 僅定義 Repository 生命週期（schema_version、close），尚無磁碟資料。
- Stage 1 採 SQLite 交易與 `PRAGMA user_version`；未知較新版本拒絕寫入。
- 遷移須先使用 SQLite backup API 取得一致性副本，保存 hash 與來源版本；
  備份失敗即停止遷移。每次 migration 在交易中更新資料與版本，可重複開啟。
- 內建工具的 seed 在 Registry 階段加入，具版本及唯一鍵，重跑不覆寫使用者設定。
- 已提交 Revision 內容不可變；新編輯建立新 Revision，CAS 防止過期更新。
- 外部檔案先在受控暫存區準備，再提交資料參照；未完成資料不能顯示為成功。
- 回滾前關閉 Studio 並備份目前 DB／受管檔案，還原相容版本整組資料；
  不把舊程式直接指向較新 DB，不刪除原始來源及 Runtime 成果。
- migration 與還原測試在隔離目錄進行；未實作或未執行的流程不宣稱驗收通過。

## v001 已實作與還原

`SQLiteRepository` 在首次開啟前備份 v000，再以單一交易建立 Project／Revision
資料表、不可變版本 trigger 與索引。`application_id` 拒絕外來資料庫；
較新版本及缺少必要表的資料庫會拒絕開啟。JSON 內容以固定排序序列化並保存 SHA-256。
寫入中斷由 SQLite rollback journal 恢復；Project version 與新 Revision 同交易提交。

`repository.backup()` 產生 `.sqlite3` 與 `.json`（schema_version、sha256）。
`SQLiteRepository.restore(snapshot, new_root)` 核對 hash 與 SQLite integrity 後，
只寫入不存在的目標 DB，拒絕覆寫原工作區；再用相容版本開啟還原資料夾驗證。
v000 備份代表首次安裝前無 Studio 資料，不能拿來復原後來建立的專案。
程式回退前請另備份最新 Studio 資料與受管檔案；Stage 0 不具備讀取 v001 資料的能力。

## v002 來源副本

新增來源身分、不可變來源版本與去重 BLOB 表。來源內容及預覽與專案版本同交易提交，
單一 DB backup 即包含本階段所有受管來源。v001 升級前建立 before-v001 備份；
回退 Stage 1 時先另存目前完整 DB，再將該備份還原到新的儲存區，確認專案後使用。
回退後自然看不到 v002 才匯入的來源，因此最新備份必須另外保留。

## v003 工具目錄

新增工具版本與 hash 綁定核准表，不修改來源或 Runtime DB。
升級前按現有來源版本建立備份；回退 Stage 2 使用 before-v002 備份並另留最新 v003。
內建工具 seed 以 ID@version 去重，不覆蓋既有停用／封存狀態。

## v004 串流與用量

新增事件 sequence／project 索引、設計／執行期模型 reservation 與 usage。
完成事件、Revision 及帳本結算同交易；內層服務使用 SQLite savepoint，外層失敗整體回復。
回退 Stage 4 使用升級前 before-v003 備份；另存最新 DB 以保存串流與用量歷史。
