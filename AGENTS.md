# RoutineKeel 專案入口

以繁體中文協作。根目錄維護原始碼，共用底座在 flow_base/，各工作流在 flows/ 的同名子目錄。修改前讀對應元件的 AGENTS.md。流程專屬模型、驗收與設定由各流程維護。

開發與測試從儲存庫根目錄執行。使用 tools/release.py 建立 dist/workspace/ 測試工作區與 dist/release/ 發行檔。操作已安裝的流程時，以發行包內的 AGENTS.md 為準，透過底座執行並回報實際生命週期事件。

GitHub Releases 提供底座及各流程的 ZIP。不要提交 dist、套件成品、運行環境、模型、私人設定與成果。README 面向非技術使用者；維護詳情在 docs/。Logo 使用 assets/routinekeel-logo.png 原檔。
