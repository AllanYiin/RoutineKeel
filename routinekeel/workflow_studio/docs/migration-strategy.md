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

## v005 資料夾證據與案例

新增不可變證據圖與 Golden Case 版本表；來源 metadata 新增可選 observed_mtime_ns，
缺少時間資訊的舊來源仍可讀，時間不參與內容指紋或因果確認。
回退 Stage 5 使用 before-v004 備份並另存最新 DB，避免遺失新證據與驗收案例。

## v006 建置紀錄

新增 build_id／Revision／套件指紋與相對路徑表。建置檔案在完成完整性檢查後才登錄候選紀錄；
中斷的未登錄資料夾不會被視為可發布版本，保留於 Studio 專用 builds 供診斷。
從此階段起，完整備份須同時保存 Studio DB 與 projects 受管目錄；回退 v005
使用 before-v005 備份，保留最新整組資料，不刪除已發布版本或 Runtime 成果。

## v007 驗證報告

新增不可變驗證報告，與 build 狀態同交易提交。實際測試成果位於 builds 的 verification 子目錄，
報告以固定 Golden Case 版本與 package hash 關聯。回退 Stage 7 使用 before-v006 備份，
同時另存最新 DB 與 projects，以保留實際重播證據。

## v008–v009 工作台治理及設定

v008 新增核准及發布日誌；v009 保存非機密模型設定。升級前仍建立相應 before-vNNN 備份。
發布工作區的 `.studio_releases` 保存版本快照、journal 與目前指標；此目錄與 Studio DB／projects
都屬備份範圍。回退前先保留整組最新資料，使用相容版本的 DB 備份，不用舊程式強寫新 schema。
已安裝流程的 outputs 不隨 Studio 更新刪除；manifest 切換失敗以 journal 核對並恢復狀態。

## v010 規則確認及撤回

新增 append-only `studio_evidence_decisions`，使用持久化 sequence、完整依據快照、hash、
確認者、理由與撤回參照。UPDATE／DELETE 被觸發器阻擋；專案內容仍使用不可變 Revision。
原有 v009 資料不會自動被標記已確認。升級前保存一致性 before-v009 備份。
已實測 v009 → v010、原專案保留、備份校驗及還原到獨立 v009 目錄，並驗證 VACUUM 不改變撤回判斷。
若回退程式，先另存最新 Studio DB、projects 及發布快照，再用 before-v009 恢復到新目錄；
回退後的新確認紀錄不在 v009 備份中，不應刪除或覆蓋原 v010 資料以免遺失稽核歷史。

## v011 工具治理事件

新增不可變 `studio_tool_events`，保存註冊、核准、撤回、啟用、停用與封存的契約指紋、
record_version、狀態、操作者及理由。操作與事件同交易，事件保存失敗會回復工具變更。
升級前保存 before-v010 備份；既有工具僅新增標示為 snapshot 的狀態快照，不虛構歷史操作者。
原 tool_approvals 保留。已實測 v010 升級、停用與核准資料保留、備份驗證及獨立 v010 還原。
回退仍先保留最新 DB／projects／發布快照，再還原到新位置；v011 期間新增治理事件需另外保留。
