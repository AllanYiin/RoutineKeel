# 發行檢核（Stage 13 草稿，尚未准予發布）

本表以 `test-report.md` 的當前證據為準。發布與上傳需另有明確授權；打包成功本身不代表可發布。

| Gate | 目前 | 證據／待辦 |
|---|---|---|
| 版本與相容 | 本機已核對 | flow_base 0.2.12、字幕流程 0.3.4、複製範例 0.2.2；schema v004 保留舊四表，舊版底座不能直接寫新 schema。 |
| 三 ZIP／SHA256SUMS | 通過本機建置 | `tools/release.py` → Valid YES；三個 ZIP 的 SHA256SUMS 逐檔正確；底座包含 Worker／UI 入口、使用指南及 source.zip 的維護文件。 |
| Stage 0–9 | 通過已記錄階段驗證 | 各階段獨立提交，Stage 9 `9ead2a4`。 |
| Stage 10 MCP | Windows／Linux 本機通過 | 提交 `16d0fd6`；MCP 只入列並回 `execution_id`，獨立 Worker 執行；Windows SDK Client 重連、v004、取消、佇列競爭、netstat 無監聽 Port 與乾淨 0.2.12 發行包實測成功；WSL Linux 完整回歸通過。macOS／正式 CI 未驗。 |
| Stage 11 UI | 通過 Windows 本機測試 | Stage 11 `ad753e0`；UI 模型、Tk 操作、移動專案重開及打包驗證通過。macOS／Linux GUI 未實測。 |
| Stage 12 AC／M | **未通過** | M7 在 C: NVMe 的 5,000 Run p95 9.78 ms 已具證據；M2 的全域追蹤覆蓋、macOS／正式 CI 尚不足，`test-report.md` 逐項列出。 |
| Windows 全測 | 本機通過 | 完整回歸 382 passed、21 skipped、0 failed；含 Windows MCP netstat 無監聽 Port 與 Stage12 整合測試。 |
| Linux/macOS | Linux 本機通過，macOS 待驗證 | WSL Linux 暫存副本 390 passed／13 skipped、建置成功；Ubuntu CI 未實際執行，macOS 真機未測。`.github/workflows/platform-checks.yml` 是尚未觸發的手動矩陣。 |
| 冷啟動與回滾演練 | Windows 部分通過 | 乾淨 0.2.11 包的 `run_app.bat list/run/doctor` 已實測；乾淨 0.2.12 包的 `queue_worker.bat --once` 不需 MCP 選配即 exit 0，另有選配 MCP 安裝、獨立 Worker、Client 重連與成果實測。`test_verified_preupgrade_backup_restores_legacy_database` 驗證備份 checksum 與 SQLite backup API 還原；非技術使用者手動走查、實際舊版 wheel 回退尚未完成。 |
| 使用者資料 | 維持隔離 | `dist/`、模型、`.venv`、私人設定、成果及日誌不得提交或打包。 |
| 發布 | 未授權／未執行 | 不建立 GitHub Release、不上傳或部署。 |

回滾順序：先停止 queue-worker 與新 Run → 保留 `share_states`／`share_logs`／`outputs` → 核對新舊 DB schema → 若退回不支援 v004 的舊版底座，先按 `upgrade-and-rollback.md` 的備份校驗與還原流程處理 DB → 再替換 `flow_base`。不可只覆蓋 wheel 就讓舊版程式寫新版資料庫。
