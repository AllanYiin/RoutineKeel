# Workflow Studio 開發入口

作用域為本子樹，繼承根目錄 AGENTS.md。需求見
`routinekeel/workflow_studio/specs/requirements.txt`，進度見
`routinekeel/workflow_studio/todo.md`。

- Studio 是設計期套件；執行透過 flow_base 公開契約與 WorkflowService。
- 不改寫底座、既有字幕／複製流程，不直接寫入底座資料庫。
- Studio 資料獨立保存；已提交版本不可變，更新採預期版本檢查。
- 來源及模型輸出都是資料，不能授予權限或直接執行 Shell。
- 接受來源必須可預覽；圖片保持比例；使用者可見 LLM 文字必須串流。
- 測試失敗的版本不得發布；核准綁定版本與內容指紋。
- 以原規格 Stage 0–13 驗收；明確啟用 devloop 時逐階段接續。
- 從儲存庫根執行 `uv run --project routinekeel/workflow_studio --group dev python -m pytest tests/studio -q`。
- lint／format 使用本套件 pyproject.toml 的 Ruff 設定。
- 底座回歸命令與 sandbox 替代命令見 `docs/test-report.md`（本子樹）。
- 每階段更新 todo、測試證據、回滾方式及 DEVNOTE；外部發布另需授權。
