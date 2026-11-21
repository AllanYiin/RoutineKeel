# 目錄與協作方式

原始碼、Release 與本機安裝的元件位置相同，完整結構與遷移驗證見 [目錄遷移說明](layout-migration.md)。

| 原始碼與安裝路徑 | Release 分包 |
| --- | --- |
| `flow_base/` | `flow_base-<版本>.zip` |
| `workflows/FLOW_課程影片產生字幕/` | `flow_subtitles-<版本>.zip` |
| `workflows/FLOW_檔案複製範例/` | `flow_copy-<版本>.zip` |
| `workflow_studio/` | `workflow-studio-<版本>.zip` |
| `blueprints/` | 全包內提供空目錄；私人內容不打包 |

全包 `RoutineKeel-workspace-<底座版本>.zip` 提供相同結構。
分包解壓至共同的 RoutineKeel 根目錄；不要將流程移出 workflows/。
Python 匯入名稱保留在各元件 src/ 內；共享環境、快取、日誌與各流程 outputs/ 由執行時建立。
