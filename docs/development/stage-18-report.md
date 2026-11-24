# Stage 18 Pack 與 Pattern Registry

沿用 Stage 17 的基線及共用契約。Studio `Catalog.packs` 負責 Pack 原始 ZIP、不可變資產版本、可信決策、試做收據索引與依賴關係。現有 Tool Registry 繼續負責工具允許清單。目標 Runtime 版本在採用 Pack 時檢查。

| 檔案 | 變更 |
| --- | --- |
| `registry/packs.py`、`registry/catalog.py` | 以同一 Studio 交易登錄 Pack、索引、歷史及內容指紋。狀態修改核對內容與預期修訂。查詢已鎖定版本可沿用不可變資料。 |
| `registry/pack_cli.py`、`__main__.py` | `workflow-studio pack` 支援登錄、查詢與本機確認。核准顯示完整版本指紋並要求輸入確認碼。 |
| `storage/migrations`、`storage/sqlite.py` | Studio schema 16 增加 Pack、Pattern、試做與稽核表。既有 v15 專案、訪談與 Tool Registry 原樣遷移。底座資料庫契約維持原版。 |

| 實際檢查 | 結果 |
| --- | --- |
| `python -m pytest tests/studio/test_pattern_registry.py tests/studio/test_migrations.py tests/studio/test_studio_interviews.py tests/studio/test_registry.py tests/studio/test_tool_management.py tests/studio/test_blueprint_export.py -q` | Windows 44 PASS。涵蓋同版衝突、並行決策、跨程序讀取、過期核准、Pack 依賴失效及還原。 |
| 隔離 Runtime | 實際安裝既有 Graph 入口的工作流，將版本鎖到 Registry，停用 Pack 後再實際執行且交付內容相同。新採用被拒。試做收據與核准事件在此測試使用 fixture，Production Pattern 試做由 Stage 20 驗收。 |
| `python -m pytest tests/test_pack_contracts.py -q` | Windows 28 PASS。 |
| 非互動核准回歸 | 首次測試發現 Windows `NUL` 可通過 `isatty`。修正後同一 CLI subprocess 回傳 `PATTERN_APPROVAL_REQUIRED`，修後整組 44 項通過。 |

SQLite v15 測試資料在移轉前取得具 SHA-256 的備份，還原至空目錄後以 v15 原契約重開。復原時保留目前資料庫及新證據，從備份還原至新目錄，確認完整性和訪談後用舊版重開。

本階段的 Windows 自動驗收使用測試工作區。macOS、Linux、真人終端和實際業務 Golden Cases 留待後續驗證。
