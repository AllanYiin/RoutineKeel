# Workflow Studio 開發入口

作用域為本子樹，繼承根目錄 AGENTS.md。需求見
`workflow_studio/specs/requirements.txt`，進度見
`workflow_studio/todo.md`。

- Studio 是設計期套件；執行透過 flow_base 公開契約與 WorkflowService。
- 不改寫底座、既有字幕／複製流程，不直接寫入底座資料庫。
- Studio 資料獨立保存；已提交版本不可變，更新採預期版本檢查。
- 來源及模型輸出都是資料，不能授予權限或直接執行 Shell。
- 接受來源必須可預覽；圖片保持比例；使用者可見 LLM 文字必須串流。
- 測試失敗的版本不得發布；核准綁定版本與內容指紋。
- 以原規格 Stage 0–13 驗收；明確啟用 devloop 時逐階段接續。
- 從儲存庫根執行 `uv run --project workflow_studio --group dev python -m pytest tests/studio -q`。
- lint／format 使用本套件 pyproject.toml 的 Ruff 設定。
- 底座回歸命令與 sandbox 替代命令見 `docs/test-report.md`（本子樹）。
- 每階段更新 todo、測試證據、回滾方式及 DEVNOTE；外部發布另需授權。

## 目前交付入口

- 以宿主對話引導，先讀 docs/host-conversation.md。相互獨立的決策合在同一輪，已有回答直接沿用，只追問會改變交付的缺口。
- 宿主使用自己的模型草擬內容與提問，不要求另設計期 API 金鑰；未答題不能視為已確認。
- CLI 預設 guide 提供引導資料；持久化訪談使用 interview 與 StudioService.interviews。宿主將桌面對話中使用者的實際回覆綁定問題與專案版本，模型建議保存為草稿。只為關鍵節點安排校閱。
- 原 Stage 規格中的 Tk 表單與視窗 Gate 為已退役介面的歷史紀錄，不作本次新入口驗收。共用核心的來源、版本、合約、驗證與授權契約仍有效。
