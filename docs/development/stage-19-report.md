# Stage 19 Pattern 擷取與去環境化

底座由現有工作區讀取指定 `workflow_id@version` 的已安裝 wheel。擷取只讀 Graph、原藍圖、套件完整性與綁定指紋。再由 Studio 保存帶有專案修訂、請求識別及來源證據的不可變草稿。原工作流匯入、試做、安裝和執行驗證沿用同一組公開入口。

| 路徑 | 變更 |
| --- | --- |
| `flow_base/graph/installed.py`、`flow_base/studio.py` | 從已安裝的 Graph 套件查核版本、RECORD、Graph／原藍圖／綁定指紋，轉交 Studio。擷取不載入 Adapter。 |
| `routinekeel_contracts/graph_validator.py`、`domain_assets.py` | 沿用既有選路分析並導出控制埠。命名控制埠含順序或分支標籤。 |
| `workflow_studio/patterns` | 保存資料埠映射、控制邊、選區、已知與待確認能力、移除資料指紋，並可匯出不可安裝的草稿 ZIP。 |
| Studio CLI／MCP 與 SQLite schema 17 | 加入擷取、讀回與草稿匯出入口。請求重送須與來源及參數相符；現有專案與底座資料庫不另變更。 |

| 實際檢查 | 結果 |
| --- | --- |
| `python -m pytest tests/studio/test_pattern_extraction.py tests/studio/test_pattern_registry.py tests/studio/test_migrations.py tests/studio/test_studio_interviews.py tests/studio/test_registry.py tests/studio/test_blueprint_export.py -q` | Windows PASS，33 項。真正安裝及執行三節點工作流，從已裝版本擷取隔著中間步驟的兩個節點。 |
| `uv run --locked --project flow_base --group dev --extra mcp python -m pytest tests/test_pack_contracts.py tests/test_graph_contracts.py tests/test_graph_branch_join.py tests/test_graph_iterations.py tests/test_graph_review_external.py tests/test_mcp_stdio.py -q` | Windows PASS，80 項。含既有分支、人審、迭代、MCP 重連及新工具清單。 |
| 草稿 ZIP | 實際開啟 ZIP，確認輸出為 Pattern 與擷取報告。指定 API key、絕對路徑、Token 及未獲確認的原成果均未出現在 ZIP 或日誌。 |

完整分支與人審可保留，來源條件的外部進入邊保留分支標籤。切斷選路目標而無法保留語意時保存受阻草稿。改版和重新確認由後續階段處理。來源業務測例預設只保留指紋。使用者明示可攜的參數仍須通過資料檢查。

測試資料使用隔離工作區。三平台和已安裝第三方非 Graph 工作流的擷取未執行。回滾時從 v16 的遷移前備份還原至另一目錄，配合 Stage 18 程式；保留 schema 17 的草稿證據。
