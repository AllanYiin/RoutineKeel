# 已知問題與驗證範圍（Stage 13 草稿）

依 `test-report.md` 的實際結果更新；未完成的項目不得視為已發布能力。

| 問題 | 影響與目前狀態 | 處理方式 |
|---|---|---|
| 佇列 Worker 未啟動 | MCP 提交仍會取得 `execution_id`，但工作維持 `queued`，此時沒有 `run_id` 或實際處理進度。 | 另開 `flow_base/queue_worker.*`；Worker 不是 MCP 子程序。關閉 Codex 不會清除已入列工作，重新開 Worker 可繼續領取。 |
| Windows 程序樹清理 | 官方 Python SDK stdio Client 關閉時會清理其 Server 程序樹。舊式「MCP 直接派生 supervisor」不具可靠續跑性；新版 MCP 僅入列，獨立 Worker 測試已通過。 | 不從 MCP Adapter 直接啟動執行程序。若直接呼叫舊 Service.start_workflow，仍須由非 MCP 的獨立宿主管理程序生命週期。 |
| Windows 完整測試 | 新版佇列與 Stage12 整合回歸 382 passed、21 skipped、0 failed。 | 11 個 POSIX shell 案例在 Windows 明確 skip，其餘 10 個為既有平台 skip；不能把 skip 當成跨平台通過。 |
| macOS 實機 | Finder 雙擊、Tk、程序控制與交付 rename 尚未在 macOS 實機驗收。 | 發行前在 macOS 實機測試；ZIP 已保存 `.command` 的 0755 mode，但不等於 Finder 已驗收。 |
| Linux 完整測試 | WSL `OpenClawGateway` 暫存副本的新版佇列與 Stage12 整合回歸為 390 passed／13 skipped，發行建置通過；它不等於 GitHub Actions 原 CI。 | 仍須在 Ubuntu CI 實際執行並核對 skip 原因。 |
| 預覽格式 | Tk 可顯示 PNG／GIF 與 UTF-8 文字；影片、音訊、PDF、JPEG 等沒有內建內容預覽。 | 工作台顯示真實檔名、大小、格式與不支援原因；流程本身仍可接收其宣告格式。 |
| 舊成果鏡像 | 強制終止到工作區重新開啟前，舊 `output/` 多檔案鏡像可能短暫混合。 | 以 `deliveries/current.json` 和 immutable version 判斷正式成果；重開時 journal 恢復。 |
| 第三方流程套件 | wheel SHA-256 僅驗證完整性，不是惡意程式沙箱。 | 只安裝可信來源流程；MCP 不提供任意安裝或 shell 工具。 |

所有診斷分享需先檢視已遮罩預覽，再明確匯出。RoutineKeel 不會自動上傳私人檔案、prompt 或診斷。
