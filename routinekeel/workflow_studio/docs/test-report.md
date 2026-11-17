# Studio 驗證紀錄

## 起始環境（2026-11-17）

Windows，CPython 3.12.11；本機 checkout `2397ede`，另含既有未提交變更。
變更前完整基線：383 passed、1 failed、21 skipped，197.82 秒。
既有失敗為 `test_recovery_launch_skips_model_preparation`：未提交的 launch
白話錯誤修正輸出文字，但既有測試 helper 仍將 stdout 當 JSON。Studio 未改此路徑。

`uv run` 在此 sandbox 先遇到預設 cache ACL，再遇到 pytest launcher
`Failed to canonicalize script path`。安全替代為既有環境直接執行模組：

```powershell
& routinekeel/flow_base/.venv/Scripts/python.exe -m pytest tests routinekeel/flows -q
& routinekeel/flow_base/.venv/Scripts/python.exe -m pytest tests/studio -q
```

uv cache 可指定為工作區 `dist/uv-cache`，不變更任何安全控制。
macOS／Linux 與真人 UI 尚未執行；舊底座歷史 CI 不能算成 Studio 驗證。

## Stage 0

- `flow_base/.venv/Scripts/python.exe -m pytest tests/studio tests/test_baseline_contract.py tests/test_decoupling.py -q`
  （路徑前綴 `routinekeel/`）：12 passed，3.85 秒。
- Studio 專屬環境 `python -m pytest tests/studio -q`：3 passed，0.10 秒。
- `python -m ruff check --config routinekeel/workflow_studio/pyproject.toml routinekeel/workflow_studio/src tests/studio`：通過。
- 相同範圍 `ruff format --check`：8 files already formatted。
- `uv build routinekeel/workflow_studio --out-dir dist/studio`：sdist／wheel 通過。
  sandbox canonicalize 拒絕後，同一命令經環境授權執行成功。
- wheel 以 `uv pip install --no-deps --target dist/studio-installed` 安裝，隔離模式
  `python -I` 從該目錄匯入並初始化服務，回報版本 0.1.0／schema 0。
- 未改動底座公開契約、既有 staged `.idea` 或啟動訊息修正。
- 原始需求 SHA-256：`2322d09b209ef08accc471f722c97c602226b5d66a14b3d9c9a743cbb1365378`。

回滾：撤回 Stage 0 新增套件與測試即可；此階段沒有持久化使用者資料。

## Stage 1

- Studio 專屬環境 `python -m pytest tests/studio -q`：24 passed，1.28 秒。
  覆蓋 CRUD／重開、同名專案隔離、CAS／重複提交、不可變發布版本、草稿捨棄、
  100 次建立重開、故障交易、程序退出、遷移重跑、備份／還原、較新版本及損毀拒絕。
- 底座環境 `python -m pytest tests/studio tests/test_baseline_contract.py tests/test_workflow_service.py tests/test_decoupling.py -q`：
  44 passed，19.32 秒（之後新增兩個儲存拒絕測試已於 Studio 24 項全測通過）。
- Ruff check 通過；格式化範圍限 Studio 與其測試。
- Stage 0 commit：`5543ebd`。本階段沒有修改 Runtime 契約或 Runtime DB。
- 回滾／備份實測步驟見 migration-strategy.md；既有基線失敗保持單列。

## Stage 2

- Studio 專屬環境 `python -m pytest tests/studio -q`：54 passed，2.03 秒，0 skipped。
- 包含實際 PNG／JPEG／WebP 解碼、等比尺寸、100 檔資料夾、ZIP 穿越／連結／膨脹／
  重複路徑、空白／損壞來源、跨專案拒絕、原始檔保留、來源歷史、交易回復。
- Windows ZIP 原始檔名正規化缺口已修復並回歸驗證。
- v001→v002 實測保留專案，升級前備份還原後仍是 v001 並可讀取原專案。
- Studio 增加 Pillow 13.0.0（lockfile 固定）；未增加 Runtime 依賴。
  底座單獨環境未安裝 Pillow 時，Studio 子套件測試明確 skip，須另外使用 Studio 環境執行；
  此處 54 項是實際執行結果，不以 skip 算作通過。
- Stage 1 commit：`b525708`；Stage 2 來源副本／預覽與清單設計見 source-formats.md。
- Stage 2 最後回歸：底座環境執行 Studio／baseline／decoupling 為 33 passed、3 skipped
  （Pillow 不在底座環境，三個來源測試模組明示跳過）；Studio 獨立環境重跑為 54 passed、0 skipped。

## Stage 3

- Studio 專屬環境 `python -m pytest tests/studio -q`：80 passed，2.13 秒。
- 底座環境 `python -m pytest tests/test_baseline_contract.py tests/test_decoupling.py -q`：9 passed，3.34 秒。
- Ruff check 通過。新增 Pydantic 2.14.3，Studio IR 與 Runtime 契約分離。
- 包含未知版本／欄位／強轉、缺少工具、循環／非法依賴、型別、路徑、政策、證據、
  JSON Schema 同步、版本衝突與不可變儲存 roundtrip。詳見 workflow-ir.md。
- Stage 2 commit：`6cb66ec`。本階段無 DB schema 變更；回滾前備份 Studio DB，
  舊版只可保留未知 IR 草稿，不能將其當成已驗證可發布版本。
