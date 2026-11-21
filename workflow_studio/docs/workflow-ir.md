# Workflow IR v1

此契約是設計期中介表示，不是 `flow.json` 或 Runtime Workflow。
精確 JSON Schema 位於 `src/workflow_studio/contracts/workflow_ir.schema.json`，
由 `ir.models.WorkflowIR.model_json_schema()` 產生並由測試比較。
Pydantic strict + extra=forbid 拒絕型別強轉、未知欄位、非有限數值與未知版本。

頂層必填：ir_version（1.0）、workflow_id、revision、goal、inputs、secrets、nodes、outputs、evidence。
Goal 需要描述及非空成功條件。每個 Node 必須提供工具 ID／版本、輸入 Binding、輸出 Port、
依賴、參數、前置與完成條件、副作用、Retry／Cache、平台、資源、Timeout 與證據引用。

Port 使用 file／string／integer／number／boolean／object／array，另有 required、format、schema_id。
檔案輸出須有安全的相對 POSIX path；Node 的每個輸出均須有完成條件。
Binding 以 `source` 邏輯參照及 `port` 預期型別組成。
物件／陣列需要更細的業務 schema 時，以 schema_id 精確比對，不將 object 自動視為任意相容。

| 檢查 | 規則 |
|---|---|
| 圖 | ID 唯一、依賴存在、無循環；輸入順序可非拓撲排序，Lowering 再排序 |
| 引用 | input://name、artifact://node/port、output://name、secret://name；不接受絕對路徑或任意 URL |
| 資料流 | 只能讀前置節點／祖先輸出；前置條件不能讀自己未產生的結果 |
| 工具 | Registry 提供精確 ID@version、種類、輸入輸出、平台與副作用；找不到即拒絕 |
| 相容 | 型別精確相符，需求 format／schema_id 相符，必要輸入不得綁 optional 來源 |
| Cache | content_addressed 必含 inputs／parameters／tool_version 三類指紋 |
| 外部副作用 | 不快取，不自動多次重試；工具宣告必須與節點一致 |
| LLM | 只有 llm 節點可有 LLMPolicy，且必填模型、secret 引用、最大呼叫數、輸出量、預算、Timeout |
| 證據 | 必須指向來源版本；verified／human_confirmed 必須有可信確認紀錄，不能只憑模型宣稱 |
| 版本 | 同專案 workflow_id 穩定，已接受 IR revision 依序遞增；過期 Project version 拒絕 |

`validate_ir(payload, tools)` 僅做記憶體靜態驗證，不呼叫模型或 Handler。
`ProjectService.save_ir` 另檢查實際來源版本作用域，通過後以原子交易建立 `ir_ready` 版本。
一般未完成草稿可用 add_revision 保存為 draft，不等於已通過 IR 驗證。

Stage 3 不實作 Runtime lowering、條件分支執行、工具核准或發布。
條件目前只接受 exists／nonempty／equals／sha256；不接受 eval 或 shell 表達式。
composite_branch／human_approval 類型可描述，但在後續實作前不能宣稱可執行；
Compiler 必須對未支援語意回報 IR_UNSUPPORTED_FEATURE。
修改 schema 應更新版本、相容測試與遷移，不就地改寫歷史 IR。
