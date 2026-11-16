# SQLite 遷移策略（Stage 0 設計，尚未實作）

1. 先取得 `state.initialize.lock` 並拒絕活躍寫入型 Run；以 SQLite backup API 建立一次性完整備份，不單獨複製 WAL 模式中的主檔。備份寫入工作區內指定位置並驗證可開啟、完整性與既有表筆數。
2. 讀取 schema 版本與 migration checksum；未知較新版 schema 拒絕寫入並顯示明確錯誤。舊 DB 無版本時視作 legacy baseline，不覆蓋原始 `runs.status`。
3. 每版 migration 在交易中完成 schema 修改、資料映射與完成標記；重複執行應為 no-op。SQLite 不支援的 DDL 交易行為須逐項確認，避免宣稱任意中斷皆可交易回滾。
4. 升級前、升級後測試 `PRAGMA integrity_check`、外鍵、原表筆數及既有 CLI history／cache 讀取；故障注入測試交易中斷、再次升級與雙程序競爭。
5. 失敗時關閉所有連線，以 SQLite backup API 從已驗證備份還原，並保留失敗診斷。回退程式版本若不認識新 schema，必須先停止新程式、還原升級前 DB；不直接向下修改使用者 DB。

Stage 1 須實測以上方案並記錄備份位置、版本對照、操作命令及資料保留證據。Stage 0 不修改任何 SQLite 資料。
