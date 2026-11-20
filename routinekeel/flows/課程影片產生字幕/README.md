# 課程影片產生字幕 0.4.1

本版搭配 flow_base 0.2.12。抽音訊階段依序去噪、用 Demucs 取出人聲、標準化音量。辨識與聽審共用原片時間軸，確認頁播放原音。

完成後請先開啟工作資料夾的 `artifacts/review.html`，或下載、完整解壓「字幕與確認表.zip」。確認頁可離線播放原音、比較 ASR 原文與目前文字、搜尋、篩選未填答片段、填寫確認文字並下載 CSV。另附中文欄位確認表、含時間碼的校對稿 TXT、SRT、VTT。QA.json 留給程式使用。原音不超過 24 MiB 時內嵌頁面；超過時使用 ZIP 內的相對音訊路徑。

瀏覽器暫存只作便利功能，請務必下載填答 CSV。CSV 帶有資料版本，不能套用到另一份或已更新的確認表。匯入只更新字幕，不會自動替真人核准。

```json
{
  "output": "課程影片產生字幕/outputs/你的工作資料夾",
  "csv_path": "D:/Downloads/課程影片.字幕疑義填答.csv"
}
```

將上述本次參數存為 JSON，透過底座 `action --task "課程影片產生字幕" --name review-import --params "參數檔絕對路徑"` 呼叫。output 傳工作根目錄，不是 artifacts。匯入會驗證資料版本、保留舊修訂／字幕版本、移除過期核准並重建；若重建失敗，查看錯誤與已保存的修訂，不宣告新字幕已完成。

既有工作已有 transcript、review_config、flow_context 與原音快取時，可用 `review-build` 重新產生確認頁與分享包，不用重新跑 ASR。只有 QA.json 無法重建原文或原音；它不能代替完整工作資料。


本次提供 **flow_base 0.2.12** 與字幕流程的獨立安裝包，請將兩包解壓至同一個父資料夾。flow_base 負責通用安裝、環境、下載校驗、登錄、執行與快取；本包負責抽音訊、VAD、ASR、字幕格式、疑義、聽審與核准。底座沒有字幕程式或 ASR 依賴。本包的完整原始碼在 source.zip。

## 使用前準備（Prerequisites / Requirements）

請備妥本機影片或錄音、flow_base 0.2.12 與本流程包。首次使用需要網路下載執行環境與 large-v3 模型；空間與記憶體需求見下方「環境、下載與續跑」。不必預先安裝 Python 或 uv。請保留既有設定、快取、日誌與人工修訂，更新時只替換套件提供的檔案。

## 一鍵開始（Quick Start）

將本資料夾放在 RoutineKeel/workflows/FLOW_課程影片產生字幕，底座位於 ../../flow_base；資料夾可自由命名。Windows 雙擊 run_app.bat，macOS 開啟 run_app.command，Linux 執行 `sh run_app.sh`。三個薄入口都委派同一底座，不各自維護安裝邏輯。不必預先安裝 uv 或 Python；首次需要網路，模型與套件現場取得。

準備完成後輸入 `/input 影片的完整路徑`，查看計畫，輸入 `/run` 開始。也可在共用入口輸入「把『課程.mp4』產生字幕」；本機規則只接受流程宣告的參數，缺項會提示。自然語言不是任意 shell 執行器。

macOS/Linux 在工作區根目錄的命令範例：

```sh
sh flow_base/start.sh run --task "課程影片產生字幕" --input "課程.mp4"
```

Windows PowerShell 將 `sh flow_base/start.sh` 替換成 `& .\flow_base\start.ps1`。`--task` 接受顯示名稱、資料夾名稱、既有識別碼或自動登錄的 flow_id，不需記憶 ai_flow01。wheel 的 ai_flow01 是內部 Python namespace，與顯示名稱無關。

0.4.0 已在 Windows x64 透過正式命令列入口以 Demucs CPU 和 Whisper CUDA 完成短音訊測試。macOS 可能需恢復執行權限或允許系統安全提示，Linux 雙擊行為依桌面設定。CPU profile 宣告 Windows x64、Linux x64、Apple Silicon macOS。

## 實際流程與限制

1. PyAV 保存 16 kHz 單聲道原音供聽審，另以 44.1 kHz 立體聲處理辨識音軌。先以頻譜去噪，再用 Hybrid Demucs 分離並取出 `vocals`，最後對整段人聲套用固定增益。有效樣本（高於 −50 dBFS）的 RMS 目標為 −20 dBFS，峰值上限 −1 dBFS，增益最多 +20 dB。模型採 10 秒分塊、1 秒交叉淡化。輸出與原音的 16 kHz 取樣點數相同。
2. Silero ONNX VAD 的語音閾值為 0.3，最短語音 250 ms，連續靜音達 2,200 ms 即可分段。所有由 VAD 判定、長度嚴格超過 3 秒的靜音（含片頭、片尾）整段略過辨識。250 ms 邊界延伸僅用在其餘區間。區段上限維持 28 秒。每段人聲單獨送辨識，字幕使用原片取樣位置。可在 `artifacts/vad_regions.json` 查看實際設定、區段與略過區間。
3. 使用 **faster-whisper + large-v3** 逐區段辨識，映回原片時間。每個完成區段保存 checkpoint；失敗後沿用已完成且指紋相符的區段。逐段 ASR 關閉第二次 VAD 與前文延續。
4. 本機繁體轉換、排版與 QA，產生疑義、原音片段、SRT、VTT。預設每行 30 字元、最多兩行、最長 12 秒、最短 0.5 秒，閱讀速率門檻 15 字元／秒。
5. **所有真實字幕預設 REVIEW_REQUIRED**，即使沒有疑義也需真人確認；有硬性 QA 問題則 FAIL。只有真人聽審後明確核准且內容指紋相符，才可 PASS。

字幕段落超過 `max_cue_seconds`（預設 12 秒）時，匯出會在該段音軌上用 Silero 尋找兩側都有語音、至少 0.20 秒的停頓，以最長停頓的中點分割。若子段仍超過時間或兩行容量，會在子段內以同一規則繼續。等長停頓優先選靠近該段中央者，再選較早者。片頭和片尾的空白不作內部切點。

有可靠字詞時間時，左右字幕跟著對應詞界分開。缺乏字詞對齊時，切點仍取自音訊，文字依可用分界和兩側發聲時間估分，並列入聽審。連續說話而找不到明顯停頓，或文字無法分成兩邊，就保留該段原有起訖與全文，交由真人處理超限問題。`artifacts/pause_splits.json` 保存實測停頓、選定的原片時間、文字對應方式與保留原因。0.4.0 起優先使用經雜湊核對的 Demucs 人聲軌，舊工作可使用聽審原音。這項規則也適用於 review-build。

Demucs 使用獨立的 CPU 環境與固定版本的 PyTorch／torchaudio 2.6.0。Whisper 使用 CTranslate2，VAD 使用 ONNX Runtime。底座依硬體快照為 Whisper 選擇 CUDA FLOAT16 或 CPU INT8，並以實際推論驗證裝置。

本機 runner 強制 **0 次 LLM API 呼叫、0 API token**，沒有 provider 或 API key 欄位，也不允許切換本流程至外部 LLM 語意路由。`text.review` 是本機審閱資料準備，沒有自動 LLM 潤飾。品質修正由真人及明確要求的有限 AI 協助完成。

主要參考使用者提供的 portable-srt 0.1.0；課程成果中的人工修訂與 DTW 強制對齊沒有自動套用。本包不宣稱所有字幕已經逐字對齊或無辨識錯誤；低信心、近似時間及抽查項目交由真人聽審。來源及差異見 PROVENANCE.md、WORKFLOW_AUDIT.md。

## 設定

將 settings.example.json 複製為 settings.json 再修改；更新流程時保留自己的 settings.json。優先序：manifest 預設 → settings.json → 明確的 --config／已驗證意圖參數。

```json
{
  "model": "whisper-large-v3",
  "profile": "auto",
  "options": {
    "language": "zh",
    "traditional_chinese": true,
    "device": "auto",
    "allow_cpu_fallback": false,
    "max_line_chars": 30,
    "max_cue_seconds": 12,
    "vad_threshold": 0.3,
    "vad_min_speech_ms": 250,
    "vad_min_silence_ms": 2200,
    "vad_skip_silence_seconds": 3.0,
    "initial_prompt": "繁體中文",
    "initial_prompt_enabled": true,
    "llm": {"enabled": false, "max_calls": 0}
  }
}
```

initial_prompt 預設「繁體中文」，啟用時空白也會補上此值；最多 300 字元。請按錄音主題補充有依據的專有名詞，不沿用其他課程詞彙。若明確停用，設定 initial_prompt_enabled=false。修改提示會影響新辨識的快取；歷史恢復仍保留原快照提示。glossary 不會自動呼叫模型改字。

CUDA 設定：

```json
{
  "profile": "asr-cuda",
  "options": {"device": "cuda", "allow_cpu_fallback": false}
}
```

準備時實際載入模型驗證，不只檢查 nvidia-smi。Windows CUDA 原生庫仍需相容環境；Linux x64 profile 宣告固定 CUDA 庫依賴。僅明確設定 allow_cpu_fallback=true 才允許同一 large-v3 改用 CPU，絕不自動換小模型。

## 環境、下載與續跑

Whisper 固定使用 Systran/faster-whisper-large-v3 的 revision `edaa852ec7e145841d8ffdb056a99866b5f0a478`，model.bin 約 3.09 GB。Demucs 官方權重另需 334,697,255 bytes，兩者均由底座下載並驗證大小和 SHA-256。首次安裝的空間估算見 plan，還需預留原音、人聲、區段與輸出的空間。兩個模型完成準備後即可離線使用。

本版本 CPU/CUDA lock 均明確鎖定 setuptools 80.9.0，處理舊 CTranslate2 匯入 pkg_resources 的相容性。先驗證依賴匯入，再下載大模型。新的依賴指紋建立新隔離環境，不刪除舊狀態。

share_states 保存模型、隔離環境、步驟產物、VAD 原音與區段 checkpoint；share_logs 保存日誌。每次執行自動取得 run_id，flow_id 與相同工作 job_id 保持穩定。重新執行相同設定可使用完整步驟快取；中斷於 ASR 時可沿用成功區段。改模型、設定或程式會按指紋使相關快取失效。下載未完成的單一檔案目前重新下載，不宣稱 HTTP Range 續傳。

入口支援 --prepare-only、--offline、--config。`--offline` 要求既有環境與模型；若連底座 bootstrap 都禁網路，另設 UV_OFFLINE=1。錯誤主畫面附日誌尾端與完整路徑，可直接重開同一入口重試。

## 產物與聽審

新格式下，以下領域檔案位於工作資料夾的 artifacts/；report.json 位於工作根目錄。

| 檔案 | 用途 |
|---|---|
| subtitles.srt、subtitles.zh-TW.srt、subtitles.vtt | 可播放的字幕；檔名中的 zh-TW 是沿用參考流程命名，實際語言以設定為準 |
| original.srt | 首次匯出的字幕；不是聲學模型的未排版逐字稿 |
| transcript.json、asr_details.json | 區段文字、原時間與 ASR 診斷 |
| vad_regions.json、region_progress.json | VAD 原片座標與逐段執行紀錄；原音檔留在共用快取 |
| review.html、review.csv、review_clips/ | 本機靜態聽審頁、疑義表及原音；無網頁伺服器或應用 UI |
| review_packet.json | 有限且遮罩的 AI 協助材料；不會自動送出 |
| QA.json、summary.json、report.json | 品質狀態、摘要、版本、模型及 flow/run/job 識別 |
| edits.json、timing_edits.json、approval.json | 人工修訂與核准，按需建立 |
| versions/ | 重建前的字幕及變更的疑義表備份 |

開啟 review.html 聽疑義原音並填寫確認文字，下載填答 CSV 後使用 review-import 匯入並重建字幕。review.csv 保留為內部相容格式，不是主要給學員填寫的表格。仍須播放完整影片確認沒有漏字／幻覺；無疑義不代表全文正確。

edits.json 的鍵使用 transcript.json／review.csv 的段落 id，值為完整修訂文字，例如：

```json
{"0": "人工聽審後確認的完整文字。"}
```

timing_edits.json 使用原片秒數，例如：

```json
{"0": {"start": 1.25, "end": 4.75}}
```

修改文字或時間後會清除不再可信的舊字級對齊。將以下參數存為工作區根目錄 rebuild.json：

```json
{"output": "請填入 plan.output 或 run.output 的完整工作路徑", "force_rebuild": true}
```

執行：

```sh
sh flow_base/start.sh action --task "課程影片產生字幕" --name review-build --params rebuild.json
```

重建不重新辨識、不呼叫 LLM；保留舊 SRT 與變更前疑義表。同一疑義未改變時保留 CSV 人工註記。修改會使舊核准指紋失效。已有人工修訂或核准時，一般 run 會拒絕覆蓋，應用 review-build 或新輸出位置。

真人完成聽審、處理硬性 QA 後，才自行建立 approve.json。reviewed_issues 填入本次 QA.json 的 issue_count，不應照抄示例數字：

```json
{"output": "請填入 plan.output 或 run.output 的完整工作路徑", "reviewer": "實際聽審者姓名", "reviewed_issues": 0, "confirm_listened": true}
```

```sh
sh flow_base/start.sh action --task "課程影片產生字幕" --name review-approve --params approve.json
```

核准會再檢查 QA、疑義數量，綁定 SRT 與疑義表 SHA-256。confirm_listened 是真人聲明，工具不能證明實際聽過，也不會代替使用者自動核准。

僅當使用者明確要求 AI 協助時，使用 review-packet 能力取得 review_packet.json 路徑（參數僅需 output）。每包最多 20 段、總共 3000 字元含前後文、每側前後文最多 60 字元；遮罩 email、URL、疑似金鑰及長數字。遮罩不能保證消除所有個資，送出前由使用者檢查。此操作不讀取完整逐字稿給模型、不呼叫 API、不核准字幕。

## 更新、遷移、分享

更新底座資料夾與本流程包；舊的 ai_flow_ASR_video 或自訂名稱可保持不變。不要同時留下兩個相同 id 的流程。設定、share_states、share_logs、字幕輸出及人工修訂應保留；新規範不能自動將舊版 complete 視為真人核准。

遷移時保留相對目錄關係、來源影片與輸出。模型與資料快取可搬移並重新校驗，.venv 及平台二進位環境現場重建。聽審重建需要 share_states 中原始 PCM 音訊，不能只搬字幕結果而刪掉全部快取。跨 OS 全流程遷移尚未真機驗收。

分享新學員只需乾淨的 flow_base 包及本流程包；已有底座者只需本流程包。不要附自己的 share_states、API key、課程內容或人工逐字稿。本發行包沒有模型、.venv 或測試課程內容。

## 0.3.2：底座偵測、流程選擇

底座 0.2.7 產生 schema_version=1 的環境快照：OS／架構、Python、記憶體與磁碟、GPU 型號／顯示記憶體、CUDA 驅動探測狀態。流程收到快照後才決定 profile／device；本流程不自行執行 nvidia-smi。底座不包含 Whisper 選擇策略。

| 情況 | 流程行為 |
|---|---|
| auto，CUDA 驅動可用或偵測到 NVIDIA 候選 | 選 asr-cuda／cuda，再以 large-v3 實際驗證 |
| auto，未偵測到 CUDA 候選 | 選 asr-cpu／cpu |
| CUDA_VISIBLE_DEVICES 明確停用 GPU | auto 選 CPU，記錄停用原因 |
| 無法確定 CUDA 狀態 | HARDWARE_UNCERTAIN，顯示探測原因，不靜默改判 CPU |
| 使用者明確選 CPU 或 CUDA | 尊重設定；profile／device 衝突時報錯 |
| CUDA 後端或模型載入失敗 | 預設停止；只有 allow_cpu_fallback=true 才允許回退 |

execution_selection 保存 requested、selected、reason、environment、backend_verified、actual_device；偵測到硬體不代表模型已可執行。Windows CUDA 依賴仍須相容，本輪不宣稱實體 Windows GPU 驗證成功。

舊 settings.json 若明確保留 profile=asr-cpu 或 device=cpu，更新後仍會尊重它。想改成自動策略，將這兩個欄位同時改為 auto，保留其他設定。新版不擅自覆寫使用者設定。profile 與原始碼版本變更可能讓計算快取失效；模型檔仍按既有資源指紋共用。

## 醒目進度與用量

實際 run 顯示工作流開始、每個節點進入、完成／快取命中與結束／失敗。抽音訊按已處理音訊秒數顯示進度，辨識按已完成 VAD 區段數顯示進度；區段尚未完成、模型驗證及其他無已知總量的階段，每約 10 秒顯示活動提示與已耗時。節點完成比例依節點數計算，不代表剩餘時間。下載另顯示檔案 bytes 比例。

結束橫幅會明確顯示 REVIEW_REQUIRED／待真人聽審。流程本身無 LLM API 呼叫，因此已登錄之流程 API 輸入（已快取／未快取）及輸出 token 都是 0；不把 Whisper 內部解碼 token 算成付費 LLM API token。

Codex／宿主對話的 token 不在底座可觀測範圍，顯示無法取得，不能誤報為零。供應商 prompt cache 與本機步驟快取是兩件事。

每次 share_logs/<flow_id>/<run_id>/ 保存 events.jsonl、live_progress.json，report.json 保存 token_usage。Codex 專案仍可各自指向流程資料夾，依外層 AGENTS.md 呼叫旁邊底座並將階段轉述到對話；工具視窗是否展開仍由宿主控制。


## 產物固定位置（0.3.2 起）

配合 flow_base 0.2.7，預設輸出到本流程 `outputs/job-影片名稱-識別碼/`，可省略 run 的 --output。根層保留 report.json 與 deliverables.json，docs/run-summary.md 提供執行摘要。

完整字幕審閱 bundle 位於 `artifacts/`：SRT、VTT、QA.json、review.html、疑義表、原音片段、transcript、edits.json、timing_edits.json、approval.json 及其關聯檔案保持相同相對位置。review.html 與中文確認表是給人操作的審閱成果，QA.json 是機器檢查資料，與字幕一起保留，不按副檔名拆到 docs 破壞原音連結。上文原本以「輸出資料夾」描述的檔案位置，在新格式皆指 artifacts 內；根層 report.json 除外。

`review-build`、`review-approve`、`review-packet` 的 output 參數仍傳整個工作資料夾，流程會解析新舊格式；不要傳 artifacts 子目錄。重建後根層報告、摘要及交付清單同步更新。修改前核對人工保護與審閱規則。

本流程不產生任務腳本，所以不建立空的 scripts/。開發用腳本留在 source.zip。既有平面輸出不自動搬移；更新保留自己的 outputs、設定、日誌與狀態。


## invalid word timeline 修復與免重新辨識恢復（0.3.4）

字級時間可能不符合嚴格驗證。新流程保留文字與有效的段落時間，將異常段落的 words 清空，保留原始 ASR 與 timing_issues.json，強制加入 review.csv／review.html 及原音聽審。這是退回段落時間，不是精確字級對齊修復；不捏造字詞位置。需要切分長句時的近似時間仍會被 QA 標示。段落本身時間不合法、跨段衝突或超出影片仍停止。

若舊工作已完成全部區段，但在組裝驗證失敗：

1. 使用本次封裝的底座 0.2.12 與流程 0.3.4，保留原本 share_states、share_logs、outputs 與原影片，勿改名或搬動原始影片。
2. 在流程資料夾開啟 recover_failed_run.bat（macOS 為 .command，Linux 為 .sh）。此入口不先載入模型、不重新執行 ASR、不下載模型。
3. 若只有一個屬於此流程的失敗工作，直接核對並恢復。多個候選或找不到時會列出狀況並停止，不會猜測。可用能力參數 JSON 指定 output；raw_asr 僅在多份不同 ASR 結果時明確指定。
4. 檢查新 report.json 的 recovery、來源 run_id、asr_calls=0、verified_regions、timing_issue_ids，再開啟 artifacts/review.html 聽審。有硬性 QA 問題維持 FAIL，其餘維持 REVIEW_REQUIRED；不會自動核准。

恢復參數範例（output 為原失敗工作根目錄）：

```json
{"output":"D:\\flow_workspace\\課程影片產生字幕\\outputs\\原工作資料夾"}
```

透過底座 `action --task <目前流程> --name recover-run --params <參數檔>` 呼叫，或 `run_app.bat --action recover-run --params <參數檔>`。不需要把整份逐字稿傳給 Agent。

恢復逐一驗證原影片、原音、VAD、checkpoint 設定／內容雜湊與已知 worker 指紋。0.3.2 原始 worker 與本版 worker 已列入相容範圍；自行修改過的 worker 或模型／設定不相符會被拒絕。舊版本缺少待執行節點指標時，只在共用 artifacts 的區段 checkpoint 中尋找，且必須通過完整來源驗證。資料不足、區段未完成或結果有歧義時停止，絕不自動重跑 ASR。

恢復保留原始辨識資料及舊失敗日誌，在 docs/before-<恢復run_id>.json 留原報告；原始 ASR 另存於該 job 的 recovery/<run_id>/raw_asr.json。只執行時間軸處理、審閱與字幕匯出，報告將辨識節點標示為 recovered_from_verified_checkpoints。一般 run 的新版本快取可能失效，所以這類故障請先使用專用恢復入口。
