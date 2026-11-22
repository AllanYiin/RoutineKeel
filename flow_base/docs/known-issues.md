# RK-NEXT 候選版的支援範圍

| 項目 | 已驗證與待驗收 |
| --- | --- |
| Windows | 完整 ZIP 冷啟動、CLI 及 MCP stdio 已實測。Studio 534 個測例均有 Windows 通過紀錄，包含首輪逾時後的獨立重跑。首次安裝的下載與環境準備時間取決於網路和磁碟。 |
| 本機 Linux | 在 WSL 原生暫存目錄執行 POSIX 啟動器、程序恢復及發布故障矩陣。完整 Studio 套件的 6 個媒體測例需 FFmpeg／ffprobe，已在 Windows 補驗。 |
| macOS、遠端 CI | 此版本尚待驗收。早期版本的通過紀錄僅供歷史查閱。 |
| 真人操作 | 非技術使用者的桌面對話走查與候選包一起做最後驗收。自動化對話只驗證協議與資料流程。 |
| 外部工具及費用 | 本機可檢查宿主提供的工具清冊、授權範圍和收據。沒有可信工具紀錄就會提示缺口。付費生成與真實對外發布留待有授權的個別任務實測。 |
| 正式交付與復原 | 核准綁定工作版本，修正保存為另一版本。需要退回套件時使用該次更新收據。資料庫降版須連同對應套件、成果與備份還原。 |

目前的操作與最後走查見[桌面對話指南](user-guide.md)。

## 早期底座版本的維護紀錄

下表保留舊版維護背景。

| 問題 | 影響與目前狀態 | 處理方式 |
|---|---|---|
| 佇列 Worker 未啟動 | MCP 提交仍會取得 `execution_id`，但工作維持 `queued`，此時沒有 `run_id` 或實際處理進度。 | 另開 `flow_base/queue_worker.*`；Worker 不是 MCP 子程序。關閉 Codex 不會清除已入列工作，重新開 Worker 可繼續領取。 |
| Windows 程序樹清理 | 官方 Python SDK stdio Client 關閉時會清理其 Server 程序樹。舊式「MCP 直接派生 supervisor」不具可靠續跑性；新版 MCP 僅入列，獨立 Worker 測試已通過。 | 不從 MCP Adapter 直接啟動執行程序。若直接呼叫舊 Service.start_workflow，仍須由非 MCP 的獨立宿主管理程序生命週期。 |
| Windows 完整測試 | 本機 382 passed／21 skipped，GitHub Actions Windows 2025 為 383 passed／21 skipped；兩邊 0 failed。 | 11 個 POSIX shell 案例在 Windows 明確 skip，其餘 10 個為既有平台 skip；不能把 skip 當成跨平台通過。 |
| macOS GUI | GitHub Actions macOS 15 的 CLI／程序控制／交付測試為 402 passed／2 skipped；Finder 雙擊與 Tk 實際視窗操作在 headless runner 未覆蓋。 | ZIP 已保存 `.command` 的 0755 mode，但仍須用互動 macOS 桌面驗證 Finder／Tk。 |
| Linux 完整測試 | WSL 暫存副本 390 passed／13 skipped；GitHub Actions Ubuntu 24.04 為 401 passed／3 skipped，建置與三 ZIP 雜湊通過。 | 保留 WSL 與 GitHub runner 的 skip 差異；不得把未執行的 GUI 視為已驗收。 |
| 舊版 0.2.9 雙擊入口 | 本機 `flow_base-0.2.9.zip` 的舊 `run_app.bat` 呼叫 Windows PowerShell 5.1 時缺少 `Get-FileHash`；同一舊 `start.ps1` 用 PowerShell 7 安裝 wheel 後可讀 `before-v001` 還原的 `run_old`。 | 此為舊發行包的環境相容限制；回滾前確認可用 PowerShell 7 或選擇經驗證的目標版本，勿在正在執行的工作區直接嘗試。 |
| 預覽格式 | Tk 可顯示 PNG／GIF 與 UTF-8 文字；影片、音訊、PDF、JPEG 等沒有內建內容預覽。 | 工作台顯示真實檔名、大小、格式與不支援原因；流程本身仍可接收其宣告格式。 |
| 舊成果鏡像 | 強制終止到工作區重新開啟前，舊 `output/` 多檔案鏡像可能短暫混合。 | 以 `deliveries/current.json` 和 immutable version 判斷正式成果；重開時 journal 恢復。 |
| 第三方流程套件 | wheel SHA-256 僅驗證完整性，不是惡意程式沙箱。 | 只安裝可信來源流程；MCP 不提供任意安裝或 shell 工具。 |

所有診斷分享需先檢視已遮罩預覽，再明確匯出。RoutineKeel 不會自動上傳私人檔案、prompt 或診斷。
