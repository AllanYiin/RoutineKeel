# Workflow Studio（開發中）

Studio 將工作描述與成功案例整理成可驗證、可重複使用的 RoutineKeel 工作流。
目前依 Stage 0–13 計畫開發；能力與實際驗收結果見 [進度](todo.md)。
尚未交付一般使用者的工作台，不能把套件可匯入當成流程編譯已完成。

從儲存庫根目錄執行開發測試：

```powershell
uv run --project routinekeel/workflow_studio --group dev python -m pytest tests/studio -q
uv run --project routinekeel/workflow_studio --group dev ruff check --config routinekeel/workflow_studio/pyproject.toml routinekeel/workflow_studio/src tests/studio
uv run --project routinekeel/workflow_studio --group dev ruff format --check --config routinekeel/workflow_studio/pyproject.toml routinekeel/workflow_studio/src tests/studio
uv build routinekeel/workflow_studio --out-dir dist/studio
```

Stage 0 服務入口為 `StudioService(MemoryRepository())`；只提供版本與儲存介面檢查。
不需 API 金鑰，不呼叫模型、不啟動背景服務，也不改動已安裝流程。
詳見 [架構](docs/architecture.md)、[遷移策略](docs/migration-strategy.md) 與
[實際測試紀錄](docs/test-report.md)。
