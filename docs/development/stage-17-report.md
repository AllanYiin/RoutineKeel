# Stage 17 領域資產契約

規格來源為 `RoutineKeel_Spec_v1.3.0.zip`（SHA-256 `0dc0b8081cf1873d68fc5979b7129e0e33e2d38b5bad4a27e9d72076966a376d`）中的開發計畫、技術規格、資料契約示例與驗收計畫。開發基線為 `a91d59bd4af8a5bdef3108b9579944c0eccc4d72`，內容樹與本機 `main/main@51ea30f` 一致。已有七個暫存的 IDE 檔案。

| 項目 | 實作及相容性 |
| --- | --- |
| Schema | 共用 `routinekeel_contracts.domain_assets` 定義 Pack、Capability、Pattern、Policy、Profile Schema 及其 JSON Schema 快照。 |
| 埠與圖形 | Capability Port 重用 Graph Port 並補上資料 schema ID 和列舉。Pattern 直接使用 Graph v2 的節點與引用驗證。Pattern 的 manifest 欄位與工作流 Blueprint 採各自的版本驗證。Studio 既有 ToolDescription 仍描述環境實作，後續 Resolver 將透過型別、權限、副作用與版本對應抽象 Capability。 |
| 載入與指紋 | `asset_loading` 限制輸入大小、巢狀深度與 ZIP 資產範圍，驗證清單、指紋與完整版本參照。YAML 禁止標籤、錨點、別名及重複鍵。分別保存原始 bytes 的 SHA-256 與正規化資料 SHA-256。 |
| 依賴 | 兩個 lockfile 加入 PyYAML 6.0.3。共用套件宣告 packaging 26.3 以檢查 Runtime 相容範圍。以現有封裝使用的 uv 0.13.6 更新 lockfile 格式。 |
| 舊資料與入口 | 此階段無 SQLite 遷移。既有 v1/v2 Blueprint、Builder 與工作流提交沿用原入口及契約。 |

Windows CPython 3.12.11，從根目錄執行。兩個環境均使用鎖定版本。

| 測試 | 結果 |
| --- | --- |
| `uv run --locked --project flow_base --group dev python -m pytest tests/test_graph_contracts.py tests/test_graph_builder.py tests/test_blueprint_install.py -q`（變更前） | PASS，32 項 |
| 同一命令加入 `tests/test_pack_contracts.py` | PASS，60 項。驗證五類 fixture 與 Schema 往返、拒絕錯誤版本及資產、Runtime 拒絕直接提交 Pattern。報告在 `dist/v130-stage17-runtime.xml`。 |
| `uv run --locked --project workflow_studio --group dev python -m pytest tests/studio/test_registry.py tests/studio/test_blueprint_export.py tests/studio/test_migrations.py -q` | PASS，15 項。環境共用舊快取時曾因 Windows ACL 失敗，改用工作區內的獨立快取後完成。報告在 `dist/v130-stage17-studio.xml`。 |
| 新 Python 檔案 Ruff lint／format 及 Git diff whitespace | PASS |

跨平台、候選包冷啟動、真人操作與模型品質留待後續階段。指紋證明資料完整性。能否採用資產與派送工具由 Registry、資格及安裝驗證接續判定。

回滾可從變更前 checkout 重建共用 wheel 及兩個鎖定環境。此階段無需還原資料庫。測試使用隔離工作區，正式安裝與發行另有驗收。
