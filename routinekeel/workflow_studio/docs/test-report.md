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
