# 發行檢核（Stage 13 人工走查未完成）

本表以 `test-report.md` 的當前證據為準。PR #3 已由儲存庫擁有者合併，舊工作流在合併後自動發布 `build-00fff1c`，但真人走查仍未回報；自動發布不等於 Stage 13 人工 Gate 通過。

| Gate | 目前 | 證據／待辦 |
|---|---|---|
| 版本與相容 | 本機已核對 | flow_base 0.2.12、字幕流程 0.3.4、複製範例 0.2.2；schema v004 保留舊四表，舊版底座不能直接寫新 schema。 |
| 三 ZIP／SHA256SUMS | 通過本機建置 | `tools/release.py` → Valid YES；三個 ZIP 的 SHA256SUMS 逐檔正確；底座包含 Worker／UI 入口、使用指南及 source.zip 的維護文件。 |
| Stage 0–9 | 通過已記錄階段驗證 | 各階段獨立提交，Stage 9 `9ead2a4`。 |
| Stage 10 MCP | 本機與三平台 CI 通過已測路徑 | 提交 `16d0fd6`；MCP 只入列並回 `execution_id`，獨立 Worker 執行；Windows SDK Client 重連、v004、取消、佇列競爭、netstat 無監聽 Port 與乾淨 0.2.12 發行包實測成功；WSL Linux 及三平台 CI 完整回歸通過。 |
| Stage 11 UI | 通過 Windows 本機測試 | Stage 11 `ad753e0`；UI 模型、Tk 操作、移動專案重開及打包驗證通過。macOS／Linux GUI 未實測。 |
| Stage 12 AC／M | 三平台自動測試通過，未覆蓋項已列出 | [PR CI run #9](https://github.com/AllanYiin/RoutineKeel/actions/runs/43987170358) 的 Ubuntu／Windows／macOS build/test 均 success；AC／M 的實測範圍見 `test-report.md`。macOS Finder／Tk 真人操作及未知硬體故障未被這組 CI 證明。 |
| Windows 全測 | 本機通過 | 完整回歸 382 passed、21 skipped、0 failed；含 Windows MCP netstat 無監聽 Port 與 Stage12 整合測試。 |
| Linux/macOS | 三平台 CI 通過，macOS GUI 未覆蓋 | Ubuntu 401 passed／3 skipped、macOS 402 passed／2 skipped、Windows 383 passed／21 skipped；三個測試 job success。macOS Finder／Tk 視窗操作仍需實機。 |
| 冷啟動與回滾演練 | Windows 部分通過 | 乾淨 0.2.11 包的 `run_app.bat list/run/doctor`、乾淨 0.2.12 包的 Worker／MCP 安裝及重連均已測。`before-v001` 備份與 checksum 通過；未修改的本機舊版 `flow_base-0.2.9.zip` wheel 經 PowerShell 7 安裝後，`history` 可讀 `run_old`。舊 `run_app.bat` 在此主機的 Windows PowerShell 5.1 因缺 `Get-FileHash` 失敗；非技術使用者手動走查仍待回報。 |
| 使用者資料 | 維持隔離 | `dist/`、模型、`.venv`、私人設定、成果及日誌不得提交或打包。 |
| 發布 | 舊工作流已自動發布，人工 Gate 未完成 | PR #3 合併後的 [run #13](https://github.com/AllanYiin/RoutineKeel/actions/runs/43994026339) 在三平台通過後發布 [build-00fff1c](https://github.com/AllanYiin/RoutineKeel/releases/tag/build-00fff1c390432ee24c01ba85bcb87376ea0004c1)；使用者已選擇保留該 Release。保留已發布版本不等於真人走查通過；後續發行須在走查完成後明確手動觸發，並重跑三平台 Gate。 |

回滾順序：先停止 queue-worker 與新 Run → 保留 `share_states`／`share_logs`／`outputs` → 核對新舊 DB schema → 若退回不支援 v004 的舊版底座，先按 `upgrade-and-rollback.md` 的備份校驗與還原流程處理 DB → 再替換 `flow_base`。不可只覆蓋 wheel 就讓舊版程式寫新版資料庫。

發行控制修訂已隨 [PR #5](https://github.com/AllanYiin/RoutineKeel/pull/5) 進入 `main`。完成真人走查並取得發行授權後，維護者才可在 GitHub Actions 手動執行 `Build and release packages`，選 `main` 並勾選 `publish_release`。工作流會重新建置並測試 Ubuntu、Windows、macOS；三平台成功後才發布 ZIP。未勾選時只執行驗證，不建立 Release。
