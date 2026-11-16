# SQLite 遷移策略（Stage 0 設計，尚未實作）

1. 先取得 `state.initialize.lock` 並拒絕活躍寫入型 Run；以 SQLite backup API 建立一次性完整備份，不單獨複製 WAL 模式中的主檔。備份寫入工作區內指定位置並驗證可開啟、完整性與既有表筆數。
2. 讀取 schema 版本與 migration checksum；未知較新版 schema 拒絕寫入並顯示明確錯誤。舊 DB 無版本時視作 legacy baseline，不覆蓋原始 `runs.status`。
3. 每版 migration 在交易中完成 schema 修改、資料映射與完成標記；重複執行應為 no-op。SQLite 不支援的 DDL 交易行為須逐項確認，避免宣稱任意中斷皆可交易回滾。
4. 升級前、升級後測試 `PRAGMA integrity_check`、外鍵、原表筆數及既有 CLI history／cache 讀取；故障注入測試交易中斷、再次升級與雙程序競爭。
5. 失敗時關閉所有連線，以 SQLite backup API 從已驗證備份還原，並保留失敗診斷。回退程式版本若不認識新 schema，必須先停止新程式、還原升級前 DB；不直接向下修改使用者 DB。

## Stage 1 已實作的 v001

`Runtime.initialize()` 於 `state.initialize.lock` 下執行 v001。舊版 `flows/runs/steps/requests` 原表與資料保留；新增 `workflow_revisions`、`jobs`、`run_executions`、`step_attempts`、`artifact_sets`、`deliveries`、`diagnostics`、`event_index`。舊 `runs` 繼續由原 API 讀寫，`legacy_run_status` view 另提供推定的狀態映射：`starting→preparing`、`waiting→queued`、`complete→succeeded`，同時保留 `original_status`。推定值不能用來宣稱舊成果已通過新驗收。新表的實際執行寫入由後續階段接入。

首次升級舊資料庫前，使用 SQLite backup API 產生 `share_states/state.sqlite3.before-v001.sqlite3` 與同名 `.sha256`；在備份存在且 checksum 相符時重試不覆蓋備份。未知較新 schema、遷移 checksum 不符或舊表必要欄位缺失會拒絕升級。每次 migration 在單一交易內建表與寫入 `PRAGMA user_version`、`schema_migrations`；中斷時交易回滾。

### 還原操作

先關閉所有 RoutineKeel 程序，確認沒有執行中的工作。於工作區根目錄，使用相容的 Python 3.12 執行下列腳本；它會先比對備份 SHA-256，再把備份還原至原 DB。請先保留目前 DB 的副本供診斷，勿在仍有寫入程序時執行。

```python
from pathlib import Path
import hashlib, sqlite3
home = Path('share_states')
backup = home / 'state.sqlite3.before-v001.sqlite3'
expected = (home / 'state.sqlite3.before-v001.sqlite3.sha256').read_text().strip()
assert hashlib.sha256(backup.read_bytes()).hexdigest() == expected
with sqlite3.connect(backup) as source, sqlite3.connect(home / 'state.sqlite3') as target:
    source.backup(target)
    assert target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
```

還原後使用與備份相容的舊版底座；不可讓舊版底座直接寫入較新版資料庫。驗證範圍見 `tests/test_db_migrations.py`：空庫、舊資料保留、重複執行、故障後重試、雙初始化、未知版本及欄位不符。
