# 課程影片產生字幕：宿主 Agent 指引

本資料夾是已封裝的流程包，不是讓 Agent 重新挑選字幕工具的空白專案。使用者要求產生字幕時，讀取 `../flow_base/AGENTS.md` 的共用執行契約，並閱讀此處 flow.json、README.md、settings.json（若存在），透過同層底座的 catalog → plan → run 執行。若共用契約缺少，先回報缺件；不得默默改用別的流程。

本流程的 Codex 專案／repo 預設就是本資料夾，對話與其他流程分開。共用執行工作區是父資料夾；執行時需允許必要的 ../flow_base、../.venv、../share_states、../share_logs。若權限受阻，回報具體路徑，不要把所有流程對話搬到同一個父目錄專案，也不要把底座複製進每個流程。不把 wheel 解開後自行執行 worker.py，不另找系統 Whisper、其他磁碟的舊專案或同類字幕技能來取代已封裝流程。只有使用者明確要求修改流程實作時，才進入 source.zip 的開發流程。

## 字幕解析必用技能

- 解析或驗證既有字幕，以及核對本流程產出的字幕時，必須先開啟 [video-subtitle-creator.skill](skills/video-subtitle-creator.skill) 壓縮包，讀取其中的 `video-subtitle-creator/SKILL.md`，並依該技能的解析與品質檢查規則操作。無法載入技能時，回報缺件並暫停字幕解析。
- SRT／VTT 先用技能內的 `scripts/validate_subtitles.py` 做本機解析與格式檢查，驗證門檻採本次工作規格。需要從 SRT 產生結構化 cue 時，再使用 `scripts/srt_to_cues.py`。Agent 只讀統計與必要診斷。
- 從影片或音訊產生字幕時，仍由本專案的 `catalog → plan → run` 執行五步流程，再用本節技能檢查字幕檔。產物判定沿用本專案的 QA 與真人聽審規則。

## 本流程規則

- 正式預設 faster-whisper + large-v3，不自動換成 tiny／small；使用 manifest/settings 的實際設定，變更前清楚說明。
- 保持五步：media.extract → speech.vad → speech.transcribe → text.review → subtitle.export。VAD 區段與原片座標、區段 checkpoint 均由已封裝程式管理。
- runner 的 LLM API 呼叫與 API tokens 強制為 0。text.review 不代表自動外部 LLM 校訂。
- 不為了「校訂」將整份逐字稿讀進 Agent 對話。只有使用者明確要求 AI 協助疑義時，呼叫宣告的 review-packet，再檢查該有限檔案；最多 20 段、3000 字元含前後文，每側前後文最多 60 字元。遮罩不保證移除所有個資，不能因此自動外傳其他資料。
- 字幕格式預設每行 30 字元、最多兩行、最長 12 秒。有硬性問題維持 FAIL；未經真人聽審維持 REVIEW_REQUIRED，即使無疑義也不能自動核准。
- Agent 不代填 confirm_listened=true，不虛構聽審者。只有使用者明確表示已聽審並要求核准，才依 README 呼叫 review-approve；疑義數量須對應最新 QA.json。
- 已有 edits.json、timing_edits.json、approval.json 時，保持人工成果；用 review-build 重建，或選新輸出，不刪除檔案來繞過保護。

## 辨識前的錄音主題與 initial_prompt

本節適用於新的語音辨識工作；既有失敗工作的恢復與續跑先遵循下方「字級時間失敗恢復」，不可為補提示而自行重辨識。

1. 在 plan／run 前，先確認錄音語言、主題與必要專有名詞。優先使用使用者本次說明、對話中已確認的課程資訊，以及使用者提供的課名、講義或大綱。檔名只作線索，不把推測當成已知內容；不讀取無關專案或沿用其他課程的詞彙。
2. 資訊不足時，簡短詢問「這段錄音主要談什麼？有哪些人名或專有名詞？」。已知的資訊不重複詢問。使用者未補充或要求直接執行時，標示主題未知，使用最低限度提示繼續，不杜撰課程內容，也不因缺少主題反覆中斷任務。不為取得主題而新增外部 LLM API 呼叫或上傳整段錄音／逐字稿。
3. 未明確指定其他語言／書寫形式時，`options.initial_prompt` 不得為空字串、全空白或 null，至少填入「繁體中文」。已確認主題時，使用「繁體中文。主題：……。常用詞：……。」的短文本，僅納入與本錄音相關且有依據的詞彙。不要貼入整份講義、預期答案或長篇命令。整份提示最多 300 字元，這是目前流程的設定限制。
4. 使用者明確指定的提示與語言優先；若已提供非空提示，保留其內容，中文且需繁體輸出時補足「繁體中文」，避免重複加入。若明確要求其他語言／簡體或停用提示，依要求處理並說明這是明確覆寫，不強塞繁體中文。`options.language` 使用流程支援的語言碼或自動偵測設定；不能把「繁體中文」當作 language 代碼。提示也不能取代既有 `traditional_chinese` 轉換與人工聽審。
5. 必須把提示實際設定到 `options.initial_prompt`。以最小差異合併本流程 `settings.json` 的 `options`，保留其他設定及使用者原有詞彙；每支新錄音重新核對主題，移除已不適用的前次主題。若本次使用 `--config`，確認較高優先序的覆寫未把提示蓋成空值，且 plan 與 run 使用同一份已核對設定。不要只在對話或 AGENTS.md 寫出提示卻未傳入流程。
6. 0.3.4 已在 `flow.json` 宣告 `options.initial_prompt` 與 `options.initial_prompt_enabled`，可透過已驗證的意圖參數、settings.json 或 --config 設定。明確停用時設定 initial_prompt_enabled=false。
7. 正式辨識前，在對話正文顯示「錄音主題／依據、語言、實際 initial_prompt」。主題未知就明寫未知；不另加一次例行核准。執行後核對本次 job 的 `snapshot.json` 中 `run_id` 與 `workflow.options.initial_prompt`，只讀必要欄位；快照 run_id 不符時不能把它當作本次證據。
8. `initial_prompt` 是辨識提示，不保證用字正確，也不是時間軸 ValidationError 的修復方式。修改提示會使相關 ASR 快取失效；已有辨識成果時不得為遵守本節而自動刪快取或從頭重跑。恢復使用原快照，若使用者要求用新提示重新辨識，才另開工作／輸出位置，保留原結果。

主題尚未確認時的最小設定如下；這是合併片段，不得用它覆蓋整份既有 settings.json：

```json
{
  "options": {
    "initial_prompt": "繁體中文"
  }
}
```

0.3.4 的程式與範例設定預設 initial_prompt="繁體中文"；啟用提示時，既有空白設定也會補上此值。錄音主題仍須由 Agent 依據使用者資訊取得，不能宣稱程式已自動理解課程內容。歷史 checkpoint 恢復保留原提示。

## 完成判定

必須從本次 report.json 核對來源、large-v3（或使用者明確指定的模型）、五個步驟、flow_id／run_id／job_id、llm_calls／llm_tokens 與 QA 狀態。另外確認 SRT／VTT、QA.json、review.html、疑義表及必要原音片段實際存在。只讀摘要和必要診斷，避免將完整 asr_details／transcript／SRT 貼進對話。

提供可開啟的結果路徑，明確區分「執行完成、待真人聽審」與「真人核准通過」。不要以其他專案現成字幕取代此次處理；如需比對舊結果，必須使用者明確指定，並驗證來源指紋與版本，仍保留本次執行證據。

## 執行可見性與自動裝置

預設 profile=auto、device=auto。底座提供硬體快照，流程選執行環境；不要自行改成 CPU 或在宿主重跑一套 CUDA 偵測。保留使用者明確的設定。結果核對 execution_selection、actual_device 與 backend_verified。

收到 workflow_started、step_started、step_completed、workflow_finished／failed 事件時，在對話正文用醒目且簡短的繁體中文說明目前階段；不要只讓使用者展開工具輸出。長工作查看本次 live_progress.json，按真實 completed／total 轉述；沒有總量就說仍在處理及已耗時。不要把節點數比例當成時間百分比，不重複啟動工作。

結束轉述 token_usage 的已快取輸入、未快取輸入與輸出；null 顯示未提供。流程 API 零用量不代表 Codex 對話零用量。未取得宿主收據就說無法取得。

## 固定成果區

使用底座預設本流程 outputs/<工作>/；plan 揭露路徑。新格式的字幕、QA、審閱頁與人工修訂都在 artifacts/ 的完整 bundle；docs/ 放執行摘要與交付說明。人審能力的 output 參數傳工作根目錄。先讀根層 report.json 的 output_layout，不把舊平面成果擅自搬移。本流程沒有交付腳本，不建立空 scripts。更新、打包及版控不得夾帶學員影片與 outputs。

## 字級時間失敗恢復

已完成全部辨識區段但 ValidationError 時，先使用宣告的 recover-run 能力，勿直接重跑辨識、刪快取或改 worker。該能力核對全部來源後只重建後段產物；若驗證不足則停止。回報 recovery.asr_calls、verified_regions、timing_issue_ids 及 source_run_id，不能把恢復當作重新辨識。異常字級對齊會退回有效段落時間並列疑義聽審，不代表已精確對齊。

## 人類可讀的主要交付（0.3.4）

- 結束時第一個連結必須是本次 artifacts/review.html，接著提供「字幕與確認表.zip」、中文確認表 CSV、校對稿 TXT 與 SRT；不要把 QA.json 當作主要交付介面。先核對 summary 的 review_table／transcript／delivery_zip 實際存在。
- 用白話摘要顯示狀態、字幕數、需聽審片段數、格式硬性問題數；FAIL 不得稱為完成驗收。確認頁提供時間碼、ASR 原文、目前校訂、中文疑義、原音與填答，頁面暫存不代表正式保存或核准。
- 使用者交回填答 CSV 時，透過 review-import（output 為工作根目錄，csv_path 為填答檔）驗證版本並重建；不將整份逐字稿送入模型，不手工繞過版本不符錯誤。文字修正未必解決時間問題，仍有硬性問題時須處理時間修訂。
- 既有已完成工作可用 review-build 更新人類交付頁面與 ZIP，無須重新辨識。其人審與原音校驗仍需通過；不改既有 ASR 的 initial_prompt。
- 原音較少時內嵌 HTML；較大時使用相對路徑，分享 ZIP 並完整解壓。單獨傳 HTML 時其他文件連結可能無法使用。
