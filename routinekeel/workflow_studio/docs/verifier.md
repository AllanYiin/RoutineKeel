# 獨立驗證與有限修復

Verifier 由 `StudioService.verify` 或 `Verifier(studio, runtime_python)` 呼叫，
接受 build_id 及固定的 case_id／case version／具名輸出對照。
每個工作流具名輸出至少有一個 Golden Case；驗收來源及預期結果綁定內容指紋，
預設逐 byte 比較，不能因生成結果錯誤而改寬鬆標準。

動態執行前檢查已登錄 ZIP hash、ZIP／流程資料夾一致、manifest、wheel RECORD、
wheel namespace 與可執行檔案 allowlist。即使生成器自行重新計算了所有 hash，
Handler 仍必須逐 byte 等於可信模板，compiled.json 必須等於已保存 IR 的重新 Lowering 結果。
完整性 hash 不等於安全沙箱；本階段拒絕執行任意生成 Python。

驗證通過的檔案 bytes 快照寫入全新的測試工作區，再以獨立 Runtime Python 的 `-I`
執行固定 runner；環境只保留必要 OS 變數，不繼承模型金鑰或 Codex Session 設定。
runner 使用 WorkflowService plan／prepare／verify／run，讀取真實 Run／Step／交付事件，
確定性流程的 registered_requests 必須為零。輸出重新核對路徑、hash 與固定預期 bytes。
每次重播是獨立目錄／run_id，不觸碰正式流程安裝及前一個成功版本。

報告包含 package hash、驗收快照 hash、來源／預期／實際 hash、Run ID、事件、差異尺寸與失敗原因。
報告一經提交不可變；只有完整通過才把該 build 標記 verified，失敗則 rejected。
重複要求會得到新的獨立測試 Run 與報告，不重複發布。

`StudioService.repair` 接受受控 proposer（可以接已授權的模型介面）提出新的 IR。
最多兩輪，每輪建立新 Revision／build 並完整重測；原本的 case 版本固定不變。
目標／成功條件／workflow_id 不允許修改。兩輪後仍失敗就回傳證據並停止，
不自行放寬條件、改動正式包或宣稱完成。目前測試使用固定 proposer，未呼叫付費模型修復。

runtime_python 必須是已安裝相容 flow_base 的 Python。未提供時使用目前 Python；
沒有 Runtime 依賴會留下明確失敗報告，不自動安裝依賴或啟動服務。
本機測試 console.log 留在該次 verification 目錄，不自動分享原始內容。
