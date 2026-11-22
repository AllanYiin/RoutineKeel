# Studio 架構與相容邊界

需求來源：使用者於 2026-11-17 提供的 `specs/requirements.txt` 原文，
以及對話 https://chatgpt.com/c/6afc4f07-0814-83ec-98d4-3648592421a8 。
原文要求逐階段停止的部分，由本次明確 devloop 授權改為驗收後接續。

目前 checkout 基線為 `2397ede645305897722ae0eb4e5e80a201553788`，含既有未提交啟動訊息修正。
底座 0.2.12、字幕 0.3.4、複製範例 0.2.2；不以遠端 main 取代本機變更。

設計期分層：UI／CLI → StudioService → 專案／來源／Discovery／IR／Registry
→ Compiler → 獨立 Verifier → 核准與版本化套件。
執行期仍透過 `flow_base.core.service.WorkflowService`，不直接寫底座資料表。

已核對的公開契約：`Workflow.schema_version=1`、`runtime='0.2'`，
`Manifest.schema_version=1`、`runtime_api='0.2'`。Step 依賴須先出現，
具名 outputs 只指向終端 Step；Step handler 使用 `module.function` 形式。
現有複製 Adapter 以 `(ctx, step, dependencies, target)` 呼叫，回傳產物路徑。
Compiler 後續需遵守這些語意，不將不支援的 IR 偽裝成有效工作流。

Studio 套件匯入不讀環境、不建立 DB、不載入 Runtime；服務採注入 Repository。
Stage 0 只有生命週期抽象及記憶體測試實例。持久化、LLM、UI 與生成器
依各階段實作後才列為可用。

Studio 狀態位於工作區 `share_states/workflow_studio/`，與
`share_states/state.sqlite3` 完全分離。來源、證據、版本、建置與報告以穩定 ID 關聯。
所有來源內容和模型輸出都是不可信資料，不能變更授權、驗收規則或命令執行能力。
