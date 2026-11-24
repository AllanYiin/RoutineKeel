# Stage 22 合成 Pattern 與品質關卡

Studio 合成入口核對目前草稿、已選用的 Pattern、試做 Pack 和訪談修訂。輸入、下游輸出、順序與選路邊均需明示對照。保留既有 Graph ID 字元契約，使用具命名空間與穩定指紋的 `rwp_` 節點識別。每筆合成保存節點與需求的精確版本。

- `patterns/composer.py` 使用既有訪談交易及 Blueprint preview/export。資料型別、選路、迴圈、關卡覆蓋及內容指紋由共用 Validator 核對。新增品質關卡檢查沿用 Runtime 品質驗收。
- 正式交付要求 Gate 出現在所有成功路徑。有效 Gate 未執行或結果不符時，Runtime 在既有交付品質狀態中回報。
- Windows Studio 的合成、推薦和既有匯出測試共 12 PASS。實際由既有 Builder 試做、安裝測試環境並執行具品質 Gate 的結果，涵蓋接受和阻擋交付。
- 底座的資產契約、Graph、分支、短迴圈、安裝及 MCP 共 79 PASS。舊正式工作流在新藍圖草稿及候選完成後仍實際執行原版本。

報告為 `dist/v130-stage22-{studio,base}.xml`。SQLite 沿用既有歷史。若需回退，從原訪談修訂重新提出藍圖，原安裝工作流及結果可繼續使用。跨平台、真人操作及新版 Adapter 資格留待後續階段。
