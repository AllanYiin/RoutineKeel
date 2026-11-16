# flow_base 一鍵安裝與啟動

## 已確認需求與範圍

- 底座只負責通用安裝、環境、資源、流程登錄、快取、日誌及能力派送；不得依賴任一任務模組或領域政策。
- 任務以 manifest、Adapter、依賴 lock 及資源宣告協作，獨立版本與發行；底座無任務時可啟動，移除單一任務不影響其他任務。
- worker 失敗顯示有限日誌尾端與路徑；資源下載顯示位元組進度與校驗完成。
- 每個流程可提供 settings.json；新增通用 actions、llm_policy 及附屬產物驗證鉤子，不新增領域命令。
- 詳細的字幕規範、相容依賴、預設及驗收屬於字幕包 README／WORKFLOW_AUDIT／PROVENANCE。

- 0.2.4：資料夾名稱、顯示名稱、穩定識別碼與 Python module 解耦。舊流程可直接改名；新流程省略 id 時自動持久化識別碼。重複識別碼或有歧義的名稱必須明確報錯。
- 各流程包提供 run_app.bat／run_app.command／run_app.sh。由共用生成器產生，僅定位同層 flow_base，委派底座安裝、準備該流程並進入指定流程的共用互動入口。
- 流程入口不寫死 id、資料夾名稱或絕對路徑；以入口所在資料夾定位 flow.json。無底座包時清楚提示放入同層，不猜測下載來源。
- 準備流程僅安裝宣告資源，不執行影片處理、不呼叫 LLM。預設首次需要網路，可用 --offline／--prepare-only／--config。

- 同一底座支撐多個流程，根目錄維持 .venv、share_logs、share_states、flow_base，以及可自由命名的流程資料夾。
- Windows 雙擊 run_app.bat；macOS 雙擊 run_app.command；Linux 執行 run_app.sh。三者由 scripts/project_launcher.py 生成，不另建獨立安裝邏輯。
- Python 與 uv 都可事先不存在。先用平台內建 shell 下載且校驗 uv，再由 uv 取得 Python 3.12、建立 .venv、安裝底座。
- 既有 start.ps1/start.sh 保留為共用底層 bootstrap；Windows 多個 uv 路徑須選取單一可用執行檔，驗證成功後才處理 .venv。
- 首次建立與重試可觀察；錯誤保留視窗、顯示繁體中文說明，安裝紀錄存 share_logs/launcher，狀態存 share_states/launcher。
- 不新增網頁 UI，不自動安裝所有任務模型。啟動後共用終端自然語言入口，模型及流程依賴於選用任務時準備。
- 任務包、flow_id/run_id、LLM 預算與資料庫保持相容；API keys 不進 bootstrap log，聊天開始前停止安裝 transcript。

## 使用情境

scene_b_shared_tool；user_type=small_team，usage_duration=occasional，change_frequency=occasional，failure_cost=multi_user_disruption。

## 技術選型與明示例外

原 APSM 1.0 的 12 組矩陣未涵蓋終端型工作流底座。此項在原矩陣為 unsupported；本專案以 flow_base_cli_v1 擴充 profile，組合 workflow_runtime/shared_workspace/terminal/python_cli。不得為了通過 web 模板檢查新增 API server、port、瀏覽器或前端。

來源 launcher 與 validator 保留；新增明確 profile 分支，沿用生成鏈。source.zip 保存完整開發規格與 scripts；學員包只展示三個一鍵入口、原 bootstrap、wheel、README 與 source.zip，不放開發資料夾。

requirements.txt 從 pyproject.toml 固定依賴生成供維護參考；安裝仍以 wheel 與鎖定版本為準。無 .env、port、.runtime 需求；runtime metadata 明確映射到使用者指定的 share_states/launcher，日誌映射到 share_logs/launcher。.venv 現場建立且不打包。

## 互動與狀態

唯一主任務：準備環境並進入工作流程入口。狀態：檢查工具 → 準備環境 → ready → chat；錯誤為 failed，使用者可直接重新雙擊。

首屏只有目前階段及進度輸出（must-see-now/status-feedback）；錯誤時出現可複製診斷、日誌路徑與重試提示（error-only/exception-handling）；流程參數在進入 chat 後出現（next-step-only）；完整環境及歷史以 README、inspect/history 查閱（on-demand-reference），不預先列出大量 schema（keep-off-first-viewport）。

## 驗收

- 壓縮包無 .venv、模型、API key 或 PowerShell 測試 runtime。
- 無 uv/Python 3.12 的 Linux 乾淨解壓測試可自動準備；第二次不重建 .venv。
- PowerShell 實際測試多 uv 選取、失效候選、完整 bootstrap 語法；Windows CMD 雙擊與 macOS Finder 未實測須明示。
- 錯誤測試記錄 failed、保留 log 與退出碼；非互動測試不因 pause 永久阻塞。
- 專案 profile validator 嚴格檢查生成入口、來源鏈與封裝邊界，不宣稱通過原 web 模板。

## 領域實作驗收位置

字幕實作以使用者提供的 portable-srt 0.1.0 作為可攜規範，另參照 subtitles 課程成果。領域規則與差異由 ai_flow01/WORKFLOW_AUDIT.md 記錄，不成為底座內建特例。通用路由尊重 manifest 的禁止 LLM 宣告；領域 runner 另自行執行零 API 政策。人工核准是流程語意，底座只保存回傳狀態與通用操作紀錄。

## 宿主 Agent 呼叫指引補正（2026-11-16）

發行包外層必須提供 AGENTS.md，不能只封存在 source.zip。工作區根指引導向共用契約；底座契約只描述 catalog／plan／run／action、結果證據與錯誤行為；各流程另維護領域規則。預設每流程各自作為對話專案／repo；共同父資料夾只是執行工作區，必要共用路徑存取與對話分組分開處理。既有使用者以純指引補丁修正，不變更 wheel 或模型，不使原有 ASR 快取失效。AGENTS.md 是宿主協作指引，不是能強制攔截外部 Agent 所有命令的執行閘門。

## 通用環境偵測與流程決策（0.2.6）

底座產生版本化環境快照：OS、CPU、Python、RAM、磁碟、NVIDIA 裝置及 CUDA 驅動偵測結果，區分可見、不可見、未知及被環境設定停用；不安裝或 import 任務依賴。通用 resolve_environment 鉤子將快照交給流程。流程選擇自身 profile／device 並負責後端實際驗證。舊流程未實作鉤子仍可使用。

字幕流程 0.3.1 預設 auto／auto；可用 CUDA 候選選 asr-cuda，確定無候選選 asr-cpu，偵測不明確時顯示原因、不靜默判為 CPU。明確 CPU／CUDA 設定優先，互相衝突時報錯。CUDA 後端驗證失敗預設停止，只有明確允許才回退。保留 large-v3、零 API 與真人聽審政策。環境快照及選擇理由寫入本次報告，但易變 RAM／VRAM 不納入辨識快取指紋。

## 可見進度與用量（0.2.6）

通用執行生命週期需醒目顯示開始、節點進入、完成或快取、結束或失敗；保留 JSON stdout 與事件／最新進度狀態。長工作顯示真實總量比例；未知總量顯示已耗時與活動，不虛構百分比。流程供應自己的節點名稱與結果文案，底座無領域特例。結束或失敗時顯示 provider usage 輸入總量、快取命中、未快取及輸出；未知值不可當 0，宿主對話用量不可冒充已知。


## 0.2.7 固定任務產物區

- CLI、自然語言及宿主 dispatch 的預設輸出統一為所選流程的 outputs/<輸入名稱與穩定識別碼>/；不再放 share_states/outputs。計畫必須揭露解析後位置。
- 新版流程以 manifest.output_layout=categorized-v1 宣告分類契約；docs/ 放執行摘要與交付說明，artifacts/ 放領域成果（相關檔案可作為完整 bundle），scripts/ 只放任務實際交付的腳本、按需建立且不自動執行。report.json 與 deliverables.json 為根層索引。
- 原始碼／啟動器與任務生成腳本分開；中間資料及續跑快取留 share_states，日誌留 share_logs。輸出不隨軟體更新刪除、不進發行包。
- 明確指定 --output 仍支援，計畫揭露為 explicit；宿主不得自行編造外部路徑。禁止輸出到輸入檔父目錄、共用狀態／日誌／底座／環境、流程程式區及符號連結。
- 同工作沿用固定成果目錄與 job_id，每次 run_id 不同。不同來源不得覆寫已存在工作。已有人工修訂／核准依領域保護。
- 舊流程未宣告 layout 時沿用 flat-v1；既有平面結果不自動搬移，審閱能力同時支援新舊格式。
- 分類與路徑驗證由底座負責，字幕 bundle 與人審規則由字幕流程負責，保持去耦合。


## 0.2.8／0.3.3 字級時間異常與失敗工作恢復

- 字級時間錯誤不放寬嚴格字幕驗收；有效段落保留文字與起訖，捨棄不可用的字級對齊並列入強制聽審與原音片段。保存原始字級資料與異常原因，段落時間非法仍停止。
- 已完成 ASR 的失敗工作可用流程 recover-run 能力恢復，驗證原輸入、VAD、全部 checkpoint 雜湊／設定／相容 worker 指紋；禁止重辨識、下載模型、付費 LLM 或偽造核准。找不到或歧義時停止並列候選，不猜來源。
- 恢復保留原失敗紀錄與原始 ASR，只執行時間軸處理、審閱與匯出，建立新的 action run_id。底座提供通用 action context，不內嵌字幕規則。
- 通用 run.json 在失敗時同步更新；待執行步驟保存產物目標供後續診斷。


## 0.2.9 終端與例外診斷持久化

- start.ps1 的非互動命令（包含 run、prepare、verify、action、launch --action／--prepare-only）將 stdout／stderr 即時顯示並保留至 launcher transcript，命令結束後才停止，保留真正退出碼。互動 chat 不錄製使用者輸入。
- 每次流程／能力失敗保存 error.json 與 exception.log，包含 error_id、錯誤訊息、驗證位置與 input 物件 id（如有）、呼叫堆疊及因果鏈。驗證 input、locals 與整份逐字稿不寫入診斷，已知環境密鑰遮罩。
- failure.json、report.json、events.jsonl 保存相同錯誤摘要與診斷路徑；CLI 的終端錯誤連結至 run 診斷與 launcher 診斷，不能只顯示類別名稱。
- 啟動器將 launch_id 傳入程式以關聯安裝／命令／run；bootstrap 與 command 的狀態、退出碼須區分。啟動失敗及未建立 run_id 的錯誤亦保留 launcher 診斷。
- 底座只處理通用錯誤，不包含任何字幕專屬判斷。歷史日誌缺失的細節不可事後猜測填入。


## 0.2.10 生命週期與宿主 Token

- run、action 與 demo 統一開始／節點開始／節點完成／結束或失敗橫幅；demo 必須明寫非正式處理。保留 stdout JSON，終端支援時加色，保存無 ANSI 的 console.log 與 live_status.md。
- 領域多行結果連結保持換行；流程驗收狀態不得改為通用 complete。回傳 presentation 日誌路徑，宿主指引要求在對話正文轉述。
- 以指定檔案或已驗證 Thread ID 綁定 Codex rollout。支援新版 token_usage_record、舊版 token_count 及宿主轉送的 App Server 通知，取最新累計，不重複相加；不得自動選最新視窗。
- 同一來源起訖收據計算觀測增量，缺失／重設／跨來源標示 unknown。沒有新收據的增量 0 不代表整輪無耗用。終止快照固定，提供可追查的數值來源但不複製對話內容。
- 宿主及流程 API 用量分開，沒有身分或可讀收據不阻斷流程，不估計未回報用量。不新增底座對領域程式的相依。
