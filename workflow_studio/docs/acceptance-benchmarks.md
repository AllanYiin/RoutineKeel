# 原規格 §12.1 本機基準

日期：2026-11-18。以下是指定資料、硬體與版本上的實測，不代表所有電腦或真實模型品質。
資料與機器報告保存在儲存庫根目錄 `dist/studio-gates/2026-11-18/`；不提交運行工作區及產物。

## 環境

- Windows 11 10.0.26300、CPython 3.12.11。
- Intel Core Ultra 9 275HX，24 logical CPUs，系統回報實體記憶體 102,455,611,392 bytes。
- 測試工作區位於 C 槽的本機 pytest 暫存目錄，磁碟為 Micron 2500 NVMe SSD。
- 儲存庫、Python 環境與報告目的地在 E 槽 StoreJet USB 磁碟；未宣稱 E 槽是 SSD。
- Studio 0.1.0、flow-base 0.2.12、pydantic 2.14.3、filelock 3.32.7。
- 基於 `b3c207a` 的當前工作樹，另含使用者既有未提交底座變更；本輪未修改底座，不將結果冒充乾淨發行 tag 的回歸。
- 不啟動桌面、不呼叫付費模型。測試不控制其他背景負載。

`hardware-storage.json` 保存只讀硬體／磁碟對應，不含磁碟序號。一般 sandbox 的 CIM 讀取被拒絕，
以獲准的只讀提權命令取得資料；沒有修改磁碟或安全設定。

## 結果

| 原門檻 | 實測 | 判定與範圍 |
|---|---|---|
| 20 節點 IR 靜態檢查 p95 <500 ms | 200 次、10 次 warmup；p50 0.35185 ms、p95 **0.8214 ms**、max 10.622 ms | PASS；20 節點線性 DAG，含來源版本集合檢查，不含讀檔／模型 |
| 100 個來源檔案清單與基本 metadata 不呼叫模型 | 匯入 300.08 ms；清單及 metadata 0.538 ms；模型呼叫與帳本皆 0 | PASS；固定資料夾內 100 個不同 UTF-8 檔案，逐項核對大小／類型／hash |
| 100 次建立／更新／重開不損壞資料 | 100 個專案與原修訂；循環中 200 次連線開啟，另有最後整體查核；1.172 秒 | PASS；100 次過期更新拒絕，integrity_check=ok，foreign_key_check 無錯誤，模型呼叫 0 |
| 100 次不同輸入不混合成果／識別碼 | **100 次成功**；242.393 秒；100 個 run_id、delivery_id、輸入 hash 均唯一 | PASS；共用 Runtime 工作區及同名輸入路徑，100 個獨立輸出目錄；事後重核全部交付版本 |
| 20 個受控故障點不損壞前一正式版本 | 20 個具名發布故障點逐 byte 保留舊快照／wheel／成果，並重播舊版 | PASS（Windows）；另有三個真正程序中止，見 [故障矩陣](fault-matrix.md) |

100 次執行使用經 Verifier 驗收及人工 fixture 核准後正式發布的 copy-example 包，
透過獨立 Runtime Python 的公開 plan／prepare／verify／run 及 list_run_artifacts API。
每次核對實際 bytes、run 生命週期、零 registered model requests，結束後再次核對全部成果指紋。
來源內容每次不同，因此沒有借用前一次來源的快取結果；正式 manifest 與 release 指標不變。

另執行一次不同輸入重用既有輸出目錄的負例，確認 `OUTPUT_EXISTS` 且原輸出整樹 bytes 不變。
底座契約不允許不同 job 覆寫既有目錄，因此最初重用 10 個輸出槽的測試期待不正確：
它在第 11 次被正常拒絕。測試改為 100 個獨立輸出位置並保留明確拒絕負例，沒有放寬底座或省略隔離要求。
初次錯誤輸出也揭露 Windows 子程序輸出編碼問題；測試 runner 改為 UTF-8 並保存原始 console bytes。

## 固定資料與證據

- `static-20-nodes.json`：資料 SHA-256 `5b4198941b0521ad315dbeef09ddd7e7a172782888c4852b4093ed627568e30b`。
- `sources-100.json`：資料 SHA-256 `e1e890a10bd68b697ef82d223aacea9bec9df4ceaf7d266740b9a3548b8d2bc3`。
- `crud-100.json`：固定索引 0–99，每個專案保留初始修訂及更新後目標。
- `isolation-100.json`／`isolation-runs.json`：100 筆執行、交付與內容指紋；資料 SHA-256
  `ef6c1a0b1364afda61a491afa66b977172f8da9d2354a1262b15664603e11185`。
- `isolation-console.log`：完整 Runtime console；`isolation-runs.progress.json` 只表示進度，不能當成完成證據。

## 重跑

從儲存庫根目錄、已安裝 Studio dev dependencies 的 Python 執行：

```powershell
$env:STUDIO_GATE_REPORT_DIR = 'E:/PycharmProjects/RoutineKeel/dist/studio-gates/recheck'
& workflow_studio/.venv/Scripts/python.exe -m pytest tests/studio/test_performance.py tests/studio/test_regression.py tests/studio/test_end_to_end.py -q -s
```

正式結果來自多次可辨識命令：初輪前三項通過、隔離測試錯誤期待失敗；修正後隔離測試
1 passed in 245.55s；加入來源版本集合的最終靜態檢查 1 passed in 0.86s。
不要把失敗初輪記成單次四項全通過。這些測試沒有更動產品程式，未因本輪只新增測試而重跑整個桌面套件。
macOS／Linux、完整 20 低風險範例、真人操作、模型品質與其他 AC Gate 仍待，見需求審核。
