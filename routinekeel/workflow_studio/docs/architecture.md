# Studio 架構與相容邊界

需求來源：使用者於 2026-11-17 提供的 `specs/requirements.txt` 原文，
以及對話 https://chatgpt.com/c/6afc4f07-0814-83ec-98d4-3648592421a8 。
原文要求逐階段停止的部分，由本次明確 devloop 授權改為驗收後接續。

Studio 版本 0.1.0，對接底座 0.2.12 的公開 Runtime API 0.2；資料庫為 Studio 獨立 schema v014。
開發／驗收所在 commit 會隨迭代改變，最新精確版本及 CI merge commit 見 [平台驗收](platform-acceptance.md)。

設計期分層：UI／CLI → StudioService → 專案／來源／Discovery／IR／Registry
→ Compiler → 獨立 Verifier → 核准與版本化套件。
執行期仍透過 `flow_base.core.service.WorkflowService`，不直接寫底座資料表。

已核對的公開契約：`Workflow.schema_version=1`、`runtime='0.2'`，
`Manifest.schema_version=1`、`runtime_api='0.2'`。Step 依賴須先出現，
具名 outputs 只指向終端 Step；Step handler 使用 `module.function` 形式。
現有複製 Adapter 以 `(ctx, step, dependencies, target)` 呼叫，回傳產物路徑。
Compiler 遵守這些語意，不將不支援的 IR 偽裝成有效工作流。

Studio 套件匯入不讀環境、不建立 DB、不載入 Runtime；服務採注入 Repository。
`StudioService.open_workspace` 才開啟專用 SQLite 儲存區，並檢查／遷移 schema。
UI 經 controller model 與共用服務操作，CLI 目前提供 ui／projects／status，沒有常駐 HTTP Server。

Studio 狀態位於工作區 `share_states/workflow_studio/`，與
`share_states/state.sqlite3` 完全分離。來源、證據、版本、建置與報告以穩定 ID 關聯。
所有來源內容和模型輸出都是不可信資料，不能變更授權、驗收規則或命令執行能力。

來源版本與受限大小的內容副本保存於 SQLite；IR、案例、證據與工具以版本及內容指紋關聯。
Compiler 產生候選 wheel／manifest／ZIP／可重建 source，Verifier 先核對可信模板與 IR，
再用獨立 Runtime Python `-I` 執行固定 runner，實際呼叫 WorkflowService 並重核交付物。
Runtime 環境不需也不應依賴 Studio；成功案例報告通過後仍需人工核准固定內容。

設計期 Provider 產生流程候選與修復，執行期 LLM 只存在於明確宣告、另行授權的模型節點。
確定性 Handler 直接使用受控實作或有界資料解譯器，不經模型再次決策。
生成處理不能任意匯入 Python 模組；[語言限制](generated-handlers.md)與[用量／隱私](privacy-and-model-usage.md)
是執行契約的一部分。

生成與驗證完成事件和其登錄結果共用 SQLite 交易；發布另外以 FileLock、快照、journal 與
原子 manifest 切換協調檔案及資料庫。斷點後按實際已套用狀態核對，不能假設所有錯誤都自動回到舊版。
歷史 Revision／報告／核准保留；新修訂不覆寫已發布套件。備份需包含 Studio DB、projects 與發布工作區，
詳見[備份與回滾](upgrade-and-rollback.md)。

目前的工程功能證據、真實模型品質與真人操作是不同層次；尚未完成的 Gate 見[需求審核](requirement-audit.md)。
