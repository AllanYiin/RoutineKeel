# 新 Handler 的生成、測試與執行

既有工具不適合時，Discovery 可要求模型產生 `transform(text, params)`、明確的參數型別及
至少兩組不同的合成測試。可選 `data.transform_text`、`data.transform_csv`、`data.transform_json`，
分別承諾 UTF-8 文字、固定欄數且表頭唯一的 CSV、或有效且無重複鍵／非有限數值的 JSON。
這些工具只有在目前 Registry 政策允許時才提供給模型；停用的能力不會出現在生成指引中。

這條路徑能建立原本沒有固定 Handler 的資料規則，例如多條件篩選訂單、欄位投影、
數值計算與分組加總。生成內容是新的處理程式，並非把業務邏輯限制在複製／格式化等既有操作。
既有工具仍優先使用；無法在允許的能力內完成時，必須回報缺口，不宣稱已涵蓋任意 Python。

## 語言與權限邊界

產生的 Python 語法由專用 AST 解譯器處理，不交給 `eval`／`exec`，也不直接 import。
允許區域變數、if／有界 for、list／dict comprehension、索引、純資料運算及文件化 helpers。
支援記憶體內 JSON／CSV、字串處理、排序與基本數值函式；不提供 OS、網路、Shell、檔案讀寫、
動態匯入、反射、類別、巢狀函式、遞迴、while 或任意 Python 函式呼叫。
for 使用有界快照；集合請用 append 或明確賦值，不使用 in-place augmented assignment。
這是受限資料語言，不是能安全執行任意 Python 套件的 OS 沙箱。

預設限制：程式 32 KiB、AST 1500 節點／64 層；值 10 MiB、集合 20,000 項／32 層；
每次 500,000 次操作、5 秒及 64 MiB 的累計配置計數。配置計數是資料配額，不是 OS RSS 保證。
膨脹運算、循環資料、過大 range／format／CSV 及非有限數值會拒絕。
程式只收到輸入文字與複製後的參數，無法取得 Runtime Context、憑證或其他專案的物件。

## 驗證與產物

Schema／語法／權限檢查先通過，才在受限環境執行合成測試。
Compiler 輸出可檢查的 `handler_<step>.py`、`generated-handler-tests.json`、完整 IR 與流程 wheel；
wheel 內含可信解譯器及版本化程式資料，仍由原有 flow_base 的 Step／Run／ArtifactSet 執行。
工作流調度、佇列、恢復與正式成果交付沒有另建引擎。

Verifier 會獨立核對程式與 IR、重跑合成測試、確認解譯器版本，再於獨立工作區經 WorkflowService
重播使用者的 Golden Cases。**模型產生的測試不是業務驗收**，不能修改固定的成功案例來換取通過。
生成原始碼／測試屬本機候選產物，發布前仍綁定內容指紋與人工核准。

完整回應的 JSON／契約／合成測試不合格時，可在原來源、目的端及預算範圍內修正最多兩輪。
不重試未知授權、來源變動、業務證據不足或中斷的傳輸。每輪都重新串流、驗證及記錄用量；
失敗不提交新有效 Revision，也不改動原目標／成功條件。

## 相容性與目前證據

套件 namespace 同時納入 IR 與執行模板內容，避免升級後同名 Python 模組混用。
已核對的前一版模板以明確 allowlist 保存，未知模板不因自簽 hash 或檔名取得信任。
目前測試已讓 CSV 篩選及 JSON 分組加總的生成程式在真正的底座執行，並通過獨立案例重播。
模型端使用固定 provider 測試生成／修復協定，尚未將此當成真實模型推導品質證據。
單一輸入檔、目前支援的語法／資料格式、平台與人工 Gate 限制仍須在整體驗收中逐項核對。
