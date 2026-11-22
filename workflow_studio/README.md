# Workflow Studio：對話內工作流設計

在 Codex／Claude Code 對話讀取本目錄 AGENTS.md，從一句任務開始。宿主可合併相關問題，讓使用者一次修正並確認設計草案。
本版本保留專案、來源、版本、合約、編譯及驗證核心，排除 Tk 桌面版及其啟動器。

操作見 [對話內引導](docs/host-conversation.md)。CLI 預設 `guide`，另有 `projects`、`status`；不開原生視窗、不自動呼叫模型。
RK-NEXT 開發版將答覆保存在本機 Studio 專案。直接在桌面對話回答或修正草稿，記下 project_id 即可在另一個對話續編。程式入口為 `StudioService.interviews`。

原始碼、Release 與本機使用同一結構：`../flow_base/`、`../workflows/FLOW_…/`、`../blueprints/`、本目錄。
確認設計後，Studio 匯出資料藍圖。底座建置候選工作流、載入臨時工作區並跑驗收案例，通過後才可安裝到正式工作區。授權發布由使用者另行決定。

開發檢查：

```powershell
uv run --project workflow_studio --group dev python -m pytest tests/studio -q
uv run --project workflow_studio --group dev ruff check --config workflow_studio/pyproject.toml workflow_studio/src workflow_studio/scripts tests/studio
```

[隱私與模型](docs/privacy-and-model-usage.md)、[升級與回復](docs/upgrade-and-rollback.md)、[歷史驗證](docs/test-report.md)。
