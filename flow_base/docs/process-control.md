# Worker 程序控制（Stage 6）

`Runtime.run_process` 保留既有呼叫介面，控制核心位於 `execution/process.py`。所有呼叫均以參數陣列啟動、合併 stdout/stderr 並標記 `stdout_stderr_merged=true`；不把 Worker 輸出印到 CLI 或 MCP stdout。啟動前建立 Flight Recorder `process.spawn_attempt` 與本機程序證據；證據只保存命令指紋、已遮罩的 argv 形狀、執行檔 metadata fingerprint、程序 token、PID、啟動時間、退出碼、經過時間、可取得的 peak RSS、輸出大小及截斷狀態。沒有 OS 明確證據時 `oom_evidence=null`。

有 log 的輸出按每段 4 MB、最多 4 個舊段輪替，超額時保留截斷標記與 `.manifest.json`；無 log 的回傳最多 1 MB 並附截斷提示。Reader 持續排空管道，避免大量 stdout/stderr 卡住 Worker。錯誤回報只帶有限尾段；完整可保留的原始輸出留在本機受限檔案。程序證據使用 `proc_<id>.json`；最終寫入失敗時，FlowError 仍帶安全的行程證據快照，成功執行則回報 `PROCESS_EVIDENCE`，不能假裝證據完整。

逾時與取消先要求停止，再依 grace period 強制清理程序樹。Windows 使用 Job Object 的 `KILL_ON_JOB_CLOSE`；若宿主不允許加入 Job，退回 `taskkill /T /F` 並在證據中標示 `windows_job_object=false`。POSIX 使用每次呼叫的短命 guardian 程序；它監視呼叫者存活、獨立管理 Worker process group，呼叫者消失時清理該 group。Linux guardian 另為 Worker 設定 parent-death signal。這些都只在任務執行期間存在，沒有常駐服務或 Port。

`Runtime.worker` 只繼承 OS 必要路徑／暫存設定和已列出的運算環境變數，另設定 HF offline、UTF-8、無緩衝輸出；不繼承 API key、HF token、雲端憑證、`PYTHONPATH` 或其他宿主任意環境變數。這是減少意外傳遞，不代表第三方 wheel 是安全沙箱。

Windows 實測與故障注入位於 `tests/test_process_failures.py`、`tests/test_worker_env.py`，涵蓋啟動失敗、非零退出、逾時、取消、輸出超量、Worker 父程序正常或突然終止、子程序樹、證據寫入失敗與秘密環境變數。Linux 另在既有 WSL 發行版以 `tests/posix_process_smoke.py` 的 stdlib unittest 實測啟動、非零退出、逾時、取消、子程序樹及呼叫端突然終止。macOS 程序語意與 Finder 雙擊仍無實機證據，須在後續矩陣明示未覆蓋，不能宣稱 macOS 已驗收。
