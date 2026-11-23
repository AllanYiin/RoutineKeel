# Stage 26 跨環境驗收與本機候選

實作依據 `RoutineKeel_Spec_v1.3.0.zip` 中的 Stage 17–26。此版規格延續現有 v1/v2 Graph、Builder、Runtime、CLI 與 MCP stdio。正式產品候選仍使用底座／Studio 0.3.0、字幕 0.5.1、檔案複製 0.2.4。v1.3.0 是本次開發規格版次。

## 逐階段證據

| 階段 | 驗收 |
| --- | --- |
| 17 | [資產契約與安全載入](stage-17-report.md) |
| 18 | [Registry、CAS、備份與復原](stage-18-report.md) |
| 19 | [從安裝版本擷取子圖](stage-19-report.md) |
| 20 | [通用化、文字保真與勾稽試做](stage-20-report.md) |
| 21 | [40 組推薦正反例](stage-21-report.md) |
| 22 | [唯一 Builder、合成及品質 Gate](stage-22-report.md) |
| 23 | [抽象能力與環境候選](stage-23-report.md) |
| 24 | [兩道資格、鎖定、降級與重連](stage-24-report.md) |
| 25 | [依賴分析、回歸與新版本核准](stage-25-report.md) |

## 跨宿主驗收

`tests/e2e/test_pattern_portability.py` 以實際 MCP stdio 本機測試伺服器取得測例資料並完成來源工作流。Studio 從已安裝套件擷取 Pattern，在沒有來源 MCP 清冊的另一個目標環境推薦、展開並匯出。目標使用實際安裝的本機 CSV Adapter，執行資格檢查、獨立候選試做和正式安裝至測試工作區。兩個目標 Run 的回饋保存為新版本改善草稿。人審確認使用 fixture，外部 API 與付費呼叫為零。

Windows 與 WSL Linux 均產出可追查的 Pack、Pattern、Blueprint、Binding Lock、Run 與改善提案指紋。證據在 `dist/v130-stage26-e2e.json`、`dist/v130-stage26-linux-e2e.json`。

最終候選另從發行 ZIP 冷啟動，以預置的同版 CPython 建立兩個隔離環境，再以包內 Runtime、Studio 及可選 Data Adapter 重跑相同案例，1 PASS。證據為 `dist/v130-stage26-verified-e2e.xml`、`dist/v130-stage26-verified-e2e.json`。

## 平台與實際驗證

| 平台 | 證據 |
| --- | --- |
| Windows | 跨宿主驗收 PASS。參考 Pack 試做 2 PASS。實際解壓候選 ZIP、準備 Runtime 和 Studio 兩個隔離環境並執行檔案複製。字幕音訊選配環境 25 PASS。候選包布局、分包及合成複驗 18 PASS。 |
| Linux，WSL | 底座、MCP、跨宿主專項 58 PASS，1 SKIP 為 Windows 專屬的 netstat 測例。Studio 推薦、合成、演進與參考 Pack 13 PASS。最終合成與演進補跑 8 PASS。POSIX 啟動器另有 41 PASS，包含以核對 SHA-256 的 PowerShell 7.4.13 執行的測試。[PowerShell 7.4.13 官方發行](https://github.com/PowerShell/PowerShell/releases/tag/v7.4.13) |
| macOS | NOT_RUN。已將跨宿主驗收納入既有三平台 CI 工作流程。此輪未推送或啟動遠端 CI。 |

真人終端核准、真實第三方模型品質與正式發布均列為 NOT_RUN。核准 `NOT_RUN` 資格會被拒絕。Linux 及 Windows 腳本檢查範圍見 JUnit。

## 完整回歸清單

依最終 `pytest --collect-only` 取得測例名單，逐條對照 JUnit。底座及兩個工作流 710/710，Studio 566/566 均有實際 PASS。Windows 初始全測的底座診斷碼缺失已修補，Studio 初始全測的一個合成案例跨越了開發中不同 Schema 的父子程序。兩例均於同版環境重跑通過。30 項 Windows 初始平台或選配略過案例，在相應的 WSL 和音訊依賴環境各有 PASS。核對清單、首次扣項及補測來源在 `dist/v130-stage26-coverage.json`。

本機執行包含兩個工作流的回歸與代表性音訊測例，未對實際用戶的課程素材執行真實模型驗收。Ruff 全庫 lint、Studio 格式與 Git whitespace 檢查通過。

## 封裝與回滾

以 `tools/release.py --candidate v130-verified --reference-packs` 建置。標準套件在 `release/`，可選 Text／Data Pack、Data Adapter 及操作說明在 `domain-packs/`。兩組各有 SHA-256 清單。發行與正式安裝仍依人審。

已重算兩份清單中的 8 個 SHA-256，讀取各 ZIP／wheel 並核對代表性 wheel 原始碼。完整工作區 ZIP 的 SHA-256 為 `c6e6d47f629d9c070f60ae8d6838f368deb21e3cad40b665a8199eeda29f7d78`。

資料回退使用底座 v005 至 v006 和 Studio v17 至 v18 的遷移前備份。先保留 SQLite 和 Lock，再還原至新目錄核對完整性，搭配原 wheel 及已驗證工作流版本重開。既有安裝與歷史留作回復證據。
