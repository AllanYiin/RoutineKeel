# RoutineKeel 專案入口

請以繁體中文協作。對外名稱與根資料夾為 RoutineKeel；內部 flow_base、ai_flow01、ai_flow02 的套件名稱與既有契約保持相容。

## 執行任務

先讀 flow_base/AGENTS.md，再讀目標流程的 AGENTS.md、flow.json、README.md。以已封裝底座執行，不另造替代流程。每個流程可各自作為 Codex 專案；底座與共用狀態仍在共同父目錄。只有需要跨流程管理／開發時才以整個 RoutineKeel 為專案。

依據本次生命週期事件，在對話正文清楚回報開始、節點與結束／失敗。未知用量或進度不可填零或估計。字幕驗收及人審語意由字幕包決定。

## 開發任務

唯一原始碼維護區為 source/。底座在 source/base/，字幕在 source/ai_flow01/，範例在 source/ai_flow02/；修改前讀相應 AGENTS.md。source/tests/ 為共用整合測試，source/tools/ 為打包工具。

根層 flow_base/ 與各流程資料夾是可執行的發行快照，downloads/ 是提供學員的乾淨分發包。不要只改 wheel 解開後的臨時副本；修改原始碼、驗證、提高對應版本並重新打包。流程專屬能力不放入共用底座。

不要上傳 .venv、模型、影片、outputs、share_states、share_logs、Codex Session、金鑰與私人設定。根 README 面向非技術使用者；深入細節放 docs/ 或套件文件。Logo 使用 assets/routinekeel-logo.png 原檔，不擅自重繪或更改授權。

維護及驗證命令見 docs/maintenance.md。保留使用者成果；不得為更名或更新刪除舊工作紀錄。
