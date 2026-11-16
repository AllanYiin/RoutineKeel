# 0.3.3 實作與驗收範圍

本次依據使用者提供的 portable-srt-0.1.0.zip、subtitles.zip 及比對表修正 0.2.4 wheel。portable-srt 是可攜處理規範；subtitles 是特定課程成果。兩者不完全相同，不能宣稱複製所有課程專屬邏輯。

## 解耦責任

| 責任 | flow_base 0.2.8 | 課程影片產生字幕 0.3.2 |
|---|---|---|
| 啟動、Python／uv、環境建立 | 通用服務及三平台入口 | 薄入口委派底座 |
| 套件／模型 | 依宣告安裝、下載、hash 校驗、快取 | CPU/CUDA lock、large-v3 資源與記憶體策略 |
| 設定與登錄 | manifest、settings 合併、flow_id／run_id | 參數模型、預設、自然語言規則 |
| 執行 | 圖順序、鎖、產物快取、日誌 | 抽音訊、VAD、逐區 ASR、區段 checkpoint |
| 審閱與核准 | 通用 action 派送、能力白名單、執行紀錄 | QA、原音、疑義、重建、聽審、PASS 門檻 |
| LLM | 通用 optional／forbidden 宣告及路由阻擋 | 強制 forbidden、零 API 預算、有限 packet |

底座不 import ai_flow01，不含 ASR 套件、模型名稱、字幕格式或 review-approve 命令分支。流程依賴底座 API，底座不依賴特定流程。每包有自己的 wheel／source.zip，獨立版本；新增字幕功能通常只改字幕包。整合 ZIP 只是同層獨立包的集合，不是合併成單一應用。

## 相對先前 wheel 的修正

| 要求 | 本版處理 |
|---|---|
| large-v3 | 正式預設、固定 revision／SHA-256，自動選 CPU／CUDA，不自動換小模型 |
| pkg_resources 錯誤 | CPU/CUDA 明確鎖 setuptools 80.9.0，下載模型前驗證匯入 |
| VAD → ASR | VAD 獨立產物，保存區段原音與原片座標，ASR 逐段 checkpoint、hash 檢查及續跑 |
| LLM | 本機 runner 與本流程外部意圖路由強制零呼叫；有限疑義只供明確要求時人工交付 AI |
| 排版 | 沿用參考的 30 字元／行、最多兩行、最長 12 秒等規則 |
| 品質與核准 | 疑義 CSV／HTML、原音 WAV、QA；無警告仍 REVIEW_REQUIRED；硬性錯誤 FAIL；真人聲明後 hash 綁定核准 |
| 人工修訂保護 | edits／timing_edits 重建、舊 SRT／CSV 備份、拒絕一般 run 覆蓋人工修訂 |

## 已做驗證

Linux x64 下載並校驗完整 large-v3 模型，使用 CPU INT8 實際推論；沒有以 tiny 或 mocked backend 替代這項驗證。

- 約 29 秒測試 MP4，含兩段英文語音與中間靜音：完成抽音訊、保存 VAD、逐區 ASR、QA、SRT/VTT 五步；字幕保留原片時間。結果 REVIEW_REQUIRED，沒有自動核准。
- 使用者提供課程音訊中的 18 秒片段：完成 large-v3 本機辨識與繁體字幕，QA 無硬性問題仍為 REVIEW_REQUIRED。片段及其逐字稿不分發；本項不是全課程準確率基準。
- 自動測試含區段中斷後續跑、時間偏移、依賴匯入失敗先停止、GPU 不靜默回退、零 API、packet 上限、疑義原音、人工確認必填、修訂使核准失效、保留人工註記、底座不含任務依賴、移除字幕包後其他流程仍執行。
- 真實產物未被自動核准；PASS 路徑只用明確標示的合成測試資料驗證。

完整測試數量與命令見底座 source.zip 中 base/VERIFICATION.md。

## 尚未完成或不宣稱

Windows PowerShell 5.1／CMD 雙擊、macOS Finder 雙擊、實體 CUDA、跨 OS 完整遷移尚未本輪真機驗收。相容依賴修正已鎖定及 Linux 匯入驗證，不能據此宣稱 Windows 安裝已驗收。

未重跑整份長課程、未量測 WER/CER、未證明任何字幕完全正確。未整合課程專屬 DTW 強制對齊、manual overrides 或固定 prompt；有疑義或近似時間仍需人工處理。未進行真實付費 LLM 測試，本流程禁止該呼叫。下載部分檔目前重抓，不是 Range 續傳；跨程序中斷可保留完成 ASR 區段，斷電／檔案系統損毀不保證恢復。

新增自動環境選擇與通用進度／用量整合。GPU 偵測及策略測試使用明確的模擬快照，不是實體 CUDA 成功證據。既有 large-v3 CPU 短片測試是本機辨識證據；本輪完整結果以 base/VERIFICATION.md 記錄為準。

## 固定產物區

新版 manifest 宣告 categorized-v1，預設流程 outputs/<工作>/。底座建立 docs/run-summary.md 與 deliverables.json，領域完整 bundle 位於 artifacts/，腳本僅在任務交付時存在。新舊格式的人審解析由字幕包實作，底座保持領域無關。舊結果不自動搬移。


0.3.3 新增字級異常保守處理與 recover-run。因本次附件只有日誌而非原 ASR／checkpoint，不能宣稱已復原使用者的 391 區段或實際 SRT。回歸資料清楚標為合成 fixture；實際操作仍需在原工作區驗證既有資料。
