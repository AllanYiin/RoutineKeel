# 二十個發布故障點與程序中止

本矩陣對應原規格 §12.1「20 個受控故障點不得損壞上一個正式發布版本」。
每個情境獨立建立通過 Verifier 的第一版、以 fixture 決策核准並發布至隔離工作區，
再產生不同 manifest 的第二版（調整 timeout），通過試做及核准後注入故障。
既有成果檔使用固定 fixture；不操作使用者的正式工作區。

20 個故障注入點定義在原始碼的 `tests/studio/fixtures/publication_faults.json`。[執行紀錄](test-report.md)
每點必須確實觸發一次指定故障，不能以其他錯誤提早退出冒充通過。

| ID | 注入位置／變更 | 核對結果 |
|---|---|---|
| F01 | 新快照 README 寫入失敗 | 尚無新 prepared 紀錄；舊版保持有效 |
| F02 | 新快照 manifest 寫入失敗 | 同上 |
| F03 | 新快照 wheel 暫存檔只寫入前綴 | 同上，保留殘留供診斷 |
| F04 | prepared 紀錄 INSERT 被 SQLite trigger 拒絕 | 交易回復；舊版保持有效 |
| F05 | journal 寫入之前失敗 | not_applied，明確結束準備 |
| F06 | journal 寫入之後失敗 | not_applied |
| F07 | 安裝新 wheel 之前失敗 | not_applied |
| F08 | 新 wheel 原子暫存檔部分寫入 | not_applied，舊 wheel 不變 |
| F09 | 正式 manifest 切換之前失敗 | not_applied |
| F10 | 正式 manifest 已切換後拋出錯誤 | applied；補齊紀錄，再以原核准回滾 |
| F11 | current 指標寫入前失敗 | applied；同上 |
| F12 | current 指標寫入後失敗 | applied；同上 |
| F13 | 發布狀態 finalized UPDATE 失敗 | DB 回復 prepared，檔案已生效；補齊後回滾 |
| F14 | Revision published 狀態 UPDATE 失敗 | 同上 |
| F15 | wheel 完成後撤回第二版核准 | APPROVAL_REQUIRED；未切換 |
| F16 | wheel 完成後退回第二版審核 | REVIEW_RETURNED；未切換 |
| F17 | wheel 完成後封存專案 | PROJECT_INACTIVE；未切換 |
| F18 | wheel 完成後修改來源版本 | REVISION_CONFLICT；未切換 |
| F19 | wheel 完成後新增必須驗收的案例 | TEST_FAILED；未切換 |
| F20 | wheel 完成後停用所需工具 | TOOL_NOT_ALLOWED；未切換 |

每個情境都逐 byte 核對舊發布快照、舊 wheel 與既有成果。切換前錯誤保留舊 current；
切換後的完整套件不被假裝成尚未生效，透過既有 ReleaseRecovery 記錄實際狀態，
再以先前成功發布的核准版本執行明確回滾。這不是「所有錯誤都自動切回舊版」的聲明。

最後都從實際安裝資料夾取出已保留／恢復的舊包，經公開 WorkflowService 真正重播，
核對輸出 bytes、零 registered model requests、SQLite integrity_check 與 foreign_key_check。
舊版因此有內容完整與可執行的雙重證據，並非只檢查某個狀態旗標。

## 真正程序中止

另在三個位置以獨立測試子程序呼叫 `os._exit(97)`，不執行 Python finally、FileLock release 或正常 rollback：

1. manifest 寫入前：重新取得鎖，核對 not_applied，保留舊版。
2. manifest 寫入後：核對 applied，補齊後回滾。
3. SQLite 已更新新 release 狀態、交易尚未提交：重開後讀到 prepared，核對實際檔案並恢復。

三項均確認指定 exit code 與故障標記，並重核快照、成果、資料庫及舊包的實際執行。
只終止本測試建立的子程序，不終止桌面或其他服務。這涵蓋應用程序中止，
不宣稱已模擬實體斷電、控制器快取遺失或磁碟硬體損毀。

## 命令與結果

Windows／CPython 3.12.11、Studio 0.1.0、flow-base 0.2.12，測試暫存位於 C 槽。
當前工作樹保留使用者既有底座修改；本輪沒有改動產品程式。

```powershell
$env:STUDIO_GATE_REPORT_DIR = 'E:/PycharmProjects/RoutineKeel/dist/studio-gates/2026-11-18-faults'
& workflow_studio/.venv/Scripts/python.exe -m pytest tests/studio/test_edge_cases.py -q -s -x --junitxml=dist/studio-gates/2026-11-18-faults/junit.xml
& workflow_studio/.venv/Scripts/python.exe -m pytest tests/studio/test_publication_process_crash.py -q -s -x --junitxml=dist/studio-gates/2026-11-18-faults/process-junit.xml
```

- 固定矩陣：**20 passed，91.19 秒**；4 個無 prepared、11 個 not_applied、5 個 applied 後回滾。
- 程序中止：**3 passed，17.29 秒**。這是三個額外中止模式，不冒充另外三個唯一故障點。
- 每點的 JSON 保存矩陣指紋、注入次數、前後快照 hash、恢復判定、重播 run_id 及環境；兩份 JUnit 保存整體結果。
- 原始證據位於上述 dist 目錄，不提交測試工作區或產物。其他平台、完整 AC、真實模型與真人 Gate 仍待。
