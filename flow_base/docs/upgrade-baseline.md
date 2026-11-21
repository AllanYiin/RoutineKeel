# flow_base 可靠性改造基線（Stage 0）

基準：`4f7ecf25e7944497885a55b38fce70301ab480f0`，`flow_base 0.2.11`。建立本文件時產品程式碼未改動；工作樹原有 `.idea/` 已暫存檔案，與本階段無關。規格來源為 `RoutineKeel_flow_base_spec_v1.2.2.zip`，後續階段以實際程式與測試為準。

## 現有結構與相容契約

- `Runtime` (`runtime.py`) 管理 `share_states/state.sqlite3`、FileLock、環境與 Worker；`Engine` (`engine.py`) 管理 plan、run、步驟結果快取與發布；`Workspace` (`workspace.py`) 掃描同層 `flow.json` 並載入 Adapter；`Reporter` (`presentation.py`) 寫入執行事件及進度；`diagnostics.py` 保存例外資料；`cli.py` 提供命令入口。
- CLI 為 `flow_base [--workspace PATH] COMMAND`；現有命令：`list`、`catalog`、`inspect`、`history`、`chat`、`launch`、`action`、`tokens`、`plan`、`prepare`、`verify`、`run`、`demo`、`cache-export`、`cache-import`、`ask`、`dispatch`。`main()` 成功輸出 `{"ok":true,"result":...}`；失敗輸出 `{"ok":false,"error":{"code","message","details","diagnostic"}}`，錯誤碼已有相容消費者，不可任意改名。
- `Manifest` 目前 `schema_version=1`、`runtime_api="0.2"`；具 `id`、`module`、`version`、`title`、`package`、`sha256`、`output_layout`、`llm_policy`、`actions` 與 `recovery_action` 等欄位。舊流程允許 `id` 作 Python module，未宣告 `output_layout` 時為 `flat-v1`。
- `Runtime.initialize()` 目前直接建立 SQLite `steps`、`requests`、`flows`、`runs` 表及 `requests_job`、`runs_flow` 索引；WAL 與外鍵啟用。`runs` 使用 `run_id`、`flow_id`、`job_id`、`status`、`source`、`output`、`started`、`finished`、`error`。尚無 schema migration 版本。舊狀態含 `starting`、`waiting`、`preparing`、`running`、`complete`、`failed`、`interrupted`，不得推測歷史紀錄為新狀態。
- `steps` 以 `cache_key` 指向單一 artifact 與 SHA-256，讀取時重新驗證檔案；`requests` 保存 LLM request 收據。`flow_id` 隨已登錄 task 保持穩定，同一工作維持 `job_id`，每次執行新建 `run_id`。
- 根工作區有 `flow_base/`、流程目錄、`share_states/`、`share_logs/`。每次 Run 日誌為 `share_logs/<flow_id>/<run_id>/`，包含 `run.json`、`events.jsonl`、`report.json`、`error.json`、`exception.log` 等（視結果而定）。啟動診斷在 `share_logs/launcher/`。正式產物預設於流程 `outputs/<工作>/`；`report.json`、`deliverables.json` 及 `docs/run-summary.md` 由底座維護。舊 `flat-v1` 產物不自動搬移。

## 重現命令與原始結果

從 repo 根目錄執行。Windows 本機 `python` 為 3.10.10，低於 `pyproject.toml` 的 `>=3.11,<3.14`；需指定 3.12。此 sandbox 的 uv 預設 cache 不可寫，使用工作區內暫存 cache：

```powershell
$env:UV_CACHE_DIR = 'E:\PycharmProjects\RoutineKeel\.uv-cache'
uv run --locked --python 3.12 --project flow_base --group dev python tools/release.py
& 'flow_base/.venv/Scripts/python.exe' -m pytest tests workflows -q
```

`release.py` 成功建立三個 ZIP 並通過其內部底座 profile 驗證。原始 HEAD 的完整 pytest 在此 Windows 主機為 `197 passed, 15 failed, 10 skipped`。其中啟動器測試依賴未提供的 `sh`；另有 Windows 路徑、ZIP 執行位元測試與字幕隔離程序測試失敗。這是改動前基線，不能視為新階段通過。`uv run` 直接執行 pytest 曾回傳 `Failed to canonicalize script path`，改用環境內 `python -m pytest`。直接對原始碼執行 `apsm_validate.py --strict` 報缺少產生的 `run_app.*`；打包過程對產生後布局的驗證為 `Valid: YES`。現有 CI 位於 `.github/workflows/packages.yml`；未宣告 lint 命令。Windows CMD 雙擊、macOS Finder、CUDA 真機仍須另行驗收。

## Stage 0 smoke 與回滾

`tests/test_baseline_contract.py` 固定無流程工作區的 CLI 查詢及結構化錯誤欄位；不測新功能。Stage 0 只新增規範、文件與測試，回滾這些檔案即可，無資料變更。進 Stage 1 前須先修正或隔離上述環境差異，再以舊 SQLite 樣本重跑相容測試。
