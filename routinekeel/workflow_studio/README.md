# Workflow Studio（開發中）

Studio 將工作描述與成功案例整理成可驗證、可重複使用的 RoutineKeel 工作流。
目前依 Stage 0–13 計畫開發；能力與實際驗收結果見 [進度](todo.md)。
目前已有可操作的本機工作台，但仍是開發版，尚未完成整體驗收。
Windows 可雙擊 [open_studio.bat](open_studio.bat)，操作見 [工作台指南](docs/workbench.md)。

從儲存庫根目錄執行開發測試：

```powershell
uv run --project routinekeel/workflow_studio --group dev python -m pytest tests/studio -q
uv run --project routinekeel/workflow_studio --group dev ruff check --config routinekeel/workflow_studio/pyproject.toml routinekeel/workflow_studio/src tests/studio
uv run --project routinekeel/workflow_studio --group dev ruff format --check --config routinekeel/workflow_studio/pyproject.toml routinekeel/workflow_studio/src tests/studio
uv build routinekeel/workflow_studio --out-dir dist/studio
```

可透過 `StudioService.open_workspace(path)` 開啟獨立 Studio 儲存區，
以 `service.projects` 建立、更新、封存、軟刪除、恢復專案及保存不可變草稿。
修改時須提供查詢得到的 `project.version`；舊版本更新會被拒絕。
`StudioService(MemoryRepository())` 仍可作為無磁碟狀態的服務 smoke 入口。
不需 API 金鑰，不呼叫模型、不啟動背景服務，也不改動已安裝流程。
詳見 [架構](docs/architecture.md)、[遷移策略](docs/migration-strategy.md) 與
[實際測試紀錄](docs/test-report.md)。
