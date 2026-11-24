# 目錄與協作方式

原始碼、Release 與本機安裝的元件位置相同，完整結構與遷移驗證見 [目錄遷移說明](layout-migration.md)。

| 原始碼與安裝路徑 | Release 分包 |
| --- | --- |
| `flow_base/` | `flow_base-<版本>.zip` |
| `workflows/FLOW_課程影片產生字幕/` | `flow_subtitles-<版本>.zip` |
| `workflows/FLOW_檔案複製範例/` | `flow_copy-<版本>.zip` |
| `workflow_studio/` | `workflow-studio-<版本>.zip` |
| `blueprints/` | 全包內提供空目錄；私人內容不打包 |

全包 `RoutineKeel-workspace-<底座版本>.zip` 內的 `workflows/` 初始為空。待安裝的兩個參考流程 ZIP 放在 `examples/workflows/`。

Studio 訪談、不可變修訂與來源屬於設計期。底座在工作區狀態目錄的 `blueprint_drafts/` 保存來源與候選建置，在 `blueprint_jobs/` 保存節點與待辦，在 `graph_evolution/` 保存修訂比較。正式工作流仍由 `workflows/` 探索。備份時一併保存工作流、兩個元件的狀態及對應成果。[版本與驗收索引](specs/README.md)
分包解壓至共同的 RoutineKeel 根目錄；不要將流程移出 workflows/。
Python 匯入名稱保留在各元件 src/ 內；共享環境、快取、日誌與各流程 outputs/ 由執行時建立。

RK-NEXT 的模組、資料版本、測試命令與還原演練見[開發基線](specs/rk-next-baseline.md)。
