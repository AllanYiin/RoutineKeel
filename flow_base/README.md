# flow_base 0.2.15

**學員使用：解壓縮後，開啟 `flow_base` 資料夾，Windows 雙擊 `run_app.bat`、macOS 雙擊 `run_app.command`，Linux 執行 `run_app.sh`。** 不必先安裝 uv、Python、PowerShell 7 或手動執行 pip。Windows 使用內建 Windows PowerShell；命令視窗會顯示準備進度，完成後進入共用自然語言入口。

想用圖形工作台時，開啟同資料夾的 `open_ui.bat`（Windows）、`open_ui.command`（macOS），或執行 `open_ui.sh`（Linux）。可選檔、預先檢查、開始／取消、查看歷史、成果與錯誤；原有 `run_app` 入口仍可照常使用。部分格式沒有內建內容預覽時，工作台會明確顯示檔名、大小與原因。詳細操作見同資料夾的 `user-guide.md`。

需要讓 Codex 透過本機 MCP 提交工作時，先**另外開啟** `queue_worker.bat`／`.command`／`.sh`；它會逐件處理 SQLite 佇列，不由 MCP Client 啟動。MCP `submit_workflow` 立即回 `execution_id`；`get_workflow_status`／`get_workflow_result` 可在重開 Codex 後用同一 ID 查詢。Worker 未開啟時狀態保持 `queued`，不會假裝已在背景執行。MCP 選配安裝與宿主設定見同資料夾的 `mcp-local.md`。

找不到 uv 時，底層啟動器會按 OS／CPU 架構下載固定版本、校驗 SHA-256，再以它取得 Python 3.12。uv 存在但無法執行時，Windows 會嘗試後續候選及私有下載。首次啟動需要網路；後續使用快取。macOS/Linux 在沒有 uv 時需要 curl 或 wget，以及 unzip。

Windows 的 run_app.bat 只為本次 PowerShell 程序設定 ExecutionPolicy Bypass，不修改系統全域政策；組織強制政策仍可能阻擋。macOS 初次可能需依系統提示允許執行；Linux 能否直接雙擊由桌面／檔案管理器設定決定。ZIP 保留 POSIX 執行權限，但部分解壓工具可能移除權限，此時需恢復 run_app.command／run_app.sh 的可執行權限。這些作業系統限制不能由啟動器保證跳過。

以 RoutineKeel 根目錄計，啟動日誌保存在 `flow_base/share_logs/launcher`；Windows 的 `start.ps1 run` 等非互動命令會持續記錄 stdout／stderr，直到命令結束，並保留真正的退出碼。每次啟動的 preparing／ready／running／completed／failed 狀態、bootstrap／command 階段、退出碼與日誌位置存於 `flow_base/share_states/launcher`。進入互動聊天前停止 transcript；互動中執行失敗仍會保存結構化錯誤。模型與大型任務套件只在選用相應流程時下載，不會因啟動底座就下載全部模型。

三個 run_app 入口由原始碼內 `scripts/project_launcher.py` 的明示 CLI profile 生成，委派既有 start.ps1/start.sh，沒有另一套環境管理邏輯。詳細規格與開發檢查工具封裝於 source.zip，不增加學員工作區根目錄的資料夾。

0.2.2 修正 Windows PATH 同時包含多個 uv.exe 時，PowerShell 將多個路徑誤當成一個執行檔的問題。依 PATH 順序逐一執行 `uv --version`，只選一個通過檢查的完整路徑；顯示選定路徑後才進行環境修復。無需刪除電腦既有的 uv 安裝。

單一工作區、底座安裝一次、任務各自一包。原始碼與安裝共用根層的 `flow_base/`、`workflows/FLOW_<名稱>/`、`blueprints/`、`workflow_studio/` 結構；`.venv/` 在根層建立，共享狀態與日誌保存在底座內。`flow_base` 不包含任何字幕辨識程式、ASR 依賴或模型；任務包才宣告自己的能力、設定與依賴。底座提供共用環境管理、資源校驗、流程登錄、執行紀錄、快取、預算帳本與自然語言入口。

## 0.2.13 對話附件更新流程

自 0.2.14 起，第一次拿到 Studio 訪談藍圖時也可在對話說「安裝這個工作流」。宿主會取得尚缺的設定、試做候選版本，將驗收結果交給底座安裝。操作說明在隨包的 `blueprint-install.md`。

日後流程有新版，在宿主對話附上新版流程 ZIP，說「請用這個附件更新流程」即可。宿主會透過底座檢查版本、備份並更新，無需自行解壓、刪檔或蓋檔。私人設定、既有成果、模型快取與執行紀錄保留；進行中及排隊工作須先完成，更新中斷可由底座復原，也可依更新編號回退。

需先使用支援此功能的底座 0.2.13，且宿主能存取附件。此入口更新已安裝的流程包，底座本身仍依原有安裝方式升級。維護與回復方式見 `source.zip` 中的 `docs/workflow-updates.md`；宿主執行契約在隨包 `AGENTS.md`。

## 0.2.11 共用狀態初始化

同一個工作區首次被多個執行命令開啟時，底座會協調 SQLite 的初始化，避免同時切換日誌模式而中斷。既有工作狀態可繼續使用。

## 0.2.10 醒目生命週期與 Codex Session 用量

此版只更新底座；可與字幕流程 0.3.4 協作，不需要重跑 ASR 或重新下載模型。請結束正在執行的程序，將 ZIP 內的 `flow_base` 合併覆蓋至原工作區的同名資料夾，再用原入口啟動。保留 `.venv`、流程包、share_states、share_logs、outputs；啟動器會按 0.2.10 wheel 的版本及雜湊更新底座。

正式 run、流程特殊 action（例如重建／匯入／恢復）以及 demo 現在都有大幅分隔的 **WORKFLOW START → STEP START → STEP DONE → WORKFLOW END／STOP**。demo 明寫示範，沒有假裝執行實際辨識；REVIEW_REQUIRED／FAIL 等領域狀態照原結果顯示，執行結束不代表真人核准。終端支援時使用加粗青色／紅色；`NO_COLOR` 可停用顏色。節點比例不是時間比例，長任務仍使用真實進度。

stdout 保持可解析結果 JSON；人類橫幅在 stderr。每次執行的 `share_logs/<flow_id>/<run_id>/` 同時保存 `console.log`（完整可讀歷程）、`live_status.md`（最新階段）、`events.jsonl` 與 `live_progress.json`。結果 JSON 的 `presentation` 提供路徑。quiet／json 模式也保存可讀檔。Codex 可能折疊工具輸出，底座無法強制展開其 UI；隨包 AGENTS.md 要求宿主將開始、節點與結果直接轉述至對話正文。

### 綁定目前 Codex 對話

預設只讀取明確綁定的本機 Session，不任意挑最近一個視窗。若宿主傳入 `CODEX_THREAD_ID`，底座會在 `CODEX_HOME`（預設 `~/.codex`）下的 sessions／archived_sessions 找對應檔名並驗證識別碼；多份相同識別碼時要求明確檔案。也可使用 `FLOW_BASE_CODEX_THREAD_ID` 明確覆寫。底座不修改 Codex 設定、不需要 API key、不啟動另一個 Codex 對話。

目前宿主沒有傳入識別碼時，在**執行同一命令的 PowerShell**綁定已確認的檔案：

```powershell
$env:FLOW_BASE_CODEX_SESSION_FILE = 'C:\Users\你的帳號\.codex\sessions\2026\11\16\rollout-實際檔名.jsonl'
& 'D:\flow_workspace\flow_base\start.ps1' tokens --file $env:FLOW_BASE_CODEX_SESSION_FILE
# 之後在此 PowerShell 啟動 run/action，會繼承綁定；不影響其他視窗。
& 'D:\flow_workspace\flow_base\start.ps1' run --task '課程影片產生字幕' --input 'D:\課程\影片.mp4'
```

macOS/Linux：

```sh
export FLOW_BASE_CODEX_SESSION_FILE="$HOME/.codex/sessions/2026/11/16/rollout-實際檔名.jsonl"
sh /你的工作區/flow_base/start.sh tokens --file "$FLOW_BASE_CODEX_SESSION_FILE"
```

獨立讀取程式只需 Python 標準函式庫，已封裝於 wheel／source.zip：

```sh
python -m flow_base.codex_tokens --file /完整路徑/rollout-實際檔名.jsonl
python -m flow_base.codex_tokens --session-id 實際ThreadID
python -m flow_base.codex_tokens --latest
```

前兩行是推薦的明確綁定；`--latest` 僅供手動查詢，輸出會警告不保證是目前視窗，流程不會自動使用這種選擇。`--file` 可搭配 `--session-id` 再驗證身分。未安裝套件時也可從 source.zip 直接執行 `python base/src/flow_base/codex_tokens.py --file ...`。

### 顯示與計算範圍

- **對話累計**：新版 `token_usage_record.thread_token_usage`；沒有新版時才採舊版 `token_count.info.total_token_usage`。只取最新累計快照，重複事件不相加。
- **本次觀測增量**：同一來源在 run/action 開始與結束的快照差，分列總輸入、已快取、未快取與輸出。沒有起始收據、來源變動或計數倒退就標記無法計算，不拿整個 Thread 累計冒充本次用量。
- `cached_input_tokens` 包含在輸入；`reasoning_output_tokens` 包含在輸出，均不再加總。缺少 optional 欄位保留 null。Context Window 是容量，不是花掉的 token。
- 若開始和結束都只讀到相同累計，顯示觀測增量 0 **及尚未收到新增用量**。這不證明整輪對話零用量；執行命令結束後才生成的回覆不可能預先計入，也可能有同 Thread 並行活動。這是收據觀測，不是完整對話帳單或配額百分比。
- `token_usage.host_agent_usage` 保存來源、Session、起始快照、終止快照、run_delta 與收據時間；終止快照固定，report 與結束事件一致。原本 `registered_workflow_api` 保留為獨立統計，不與宿主數字相加，避免重疊計費。
- 讀取器只保留用量／識別資訊；不複製訊息、提示詞或逐字稿。初次串流掃描已綁定檔案，後續從上次位置繼續；不完整末行等待下次，壞行跳過並計數。讀不到檔案只顯示 unavailable，不阻擋工作流。

### App Server 通知來源

若已有宿主 App Server 連線，也支援將收到的 `thread/tokenUsage/updated` 原始通知逐行轉送至專用 JSONL，再把 `FLOW_BASE_CODEX_SESSION_FILE` 指向該檔，並設定 `FLOW_BASE_CODEX_THREAD_ID`。底座使用 `params.tokenUsage.total`，保存 `last` 與 `modelContextWindow`；過濾其他 Thread，沒有指定 Thread 而遇到混合通知時拒絕推測。

這是**既有連線的通知接收相容層**；本版採本機 rollout 為主要入口，沒有聲稱會自動接上另一個 Codex 視窗。App Server 客戶端須依正式協定初始化並保持通知串流；不是向公開 HTTPS URL 發 GET。若宿主環境不提供 rollout 也不轉送通知，必須顯示無法取得，不能填 0。

格式依據：[OpenAI App Server 文件](https://developers.openai.com/codex/app-server)、[官方累計 Token 記錄實作](https://github.com/openai/codex/blob/main/codex-rs/core/src/state/session.rs)。通知欄位另以本機 Codex CLI 0.167.0-alpha.4 生成的 JSON Schema 核對。

## 0.2.9 錯誤紀錄與更新

此版本只更新底座，與字幕流程 0.3.3 協作；字幕模型、VAD、字級時間軸與人工驗收規則仍由流程包維護。

結束正在執行的底座後，將本包的 `flow_base` 合併覆蓋到原工作區同名資料夾，例如 `D:\flow_workspace\flow_base`，再開啟原入口。不要刪除流程資料夾、`share_states`、`share_logs` 或 `outputs`。啟動器會依新 wheel 更新底座環境；既有任務狀態及模型快取繼續保留。

| 紀錄 | 內容 |
| --- | --- |
| `share_logs/launcher/launch_<id>.log` | Windows 啟動安裝，以及非互動命令的 stdout、stderr、退出碼。 |
| `share_logs/<flow_id>/<run_id>/error.json` | 錯誤識別碼、完整原因、驗證位置、可取得的資料列 id、程式檔案／行號／函式、因果鏈、啟動識別碼。 |
| 同 run 的 `exception.log` | 可直接閱讀的錯誤原因與呼叫堆疊。 |
| 同 run 的 `failure.json`、`report.json`、`events.jsonl` | `error_details` 關聯同一個錯誤；既有 `error` 代碼保留。尚未進入產物階段的前置失敗可能沒有 report。 |
| `share_logs/launcher/<launch_id>/<error_id>/error.json` | CLI 層錯誤；若已有 run，提供 `run_diagnostic_json`。 |

例如 Pydantic 的模型驗證發現 `invalid word timeline`，且驗證輸入含 `id: 20`，結構化紀錄會保存 `record_id: 20` 與原因，而非只留下 `ValidationError`。若例外本身沒有資料列 id，底座不推測。CLI、直接 run、流程 action、互動執行皆使用同一套診斷寫入器。

結構化診斷不序列化完整驗證輸入或 frame locals，並遮罩已知環境金鑰與敏感欄位。終端 transcript 是原樣輸出，流程自行印出的內容也可能被記錄；分享前仍應檢查內容。互動聊天不錄製整段 transcript。

新版本只改善後續執行紀錄，無法補回舊日誌從未保存的終端內容。本次更新不會自動重跑 ASR 或改寫舊 run。

## 0.2.5 更新

修復帶日誌的子程序失敗時主畫面 output 為空的問題：會顯示最後至多 8000 bytes 的 UTF-8 診斷與完整日誌路徑。資源下載每約五秒顯示位元組進度，逐檔驗證後才使用；未完成的部分檔目前重新下載。

各流程可放一份 settings.json 保存使用者設定，啟動器與命令列都讀取；明確的 --config／意圖參數可覆蓋它。發行包只提供 settings.example.json，不覆蓋使用者 settings.json。字幕流程 0.3.0 已正式預設 large-v3；CPU/CUDA 的相容依賴鎖及模型資源仍由流程包維護，底座沒有寫死辨識模型。

## 資料夾與交付方式

將各 ZIP 解壓到同一個父資料夾。交付時只有 `flow_base` 與所需的流程資料夾（名稱可自訂）；第一次啟動才產生 `.venv`、`share_logs`、`share_states`。

```text
工作區/
    .venv/          首次執行建立：底座的輕量 Python 環境
    share_logs/     共用日誌：flow_id/run_id/；安裝日誌另按環境區分
    share_states/   流程登錄、執行狀態、預算帳本、快取、模型與隔離環境
    flow_base/      三平台 run_app、底層 start、底座 wheel、README、完整原始碼 ZIP
    課程影片產生字幕/  字幕流程：三平台 run_app、flow.json、任務 wheel、README、source.zip
    檔案複製範例/    選用的第二流程，同樣提供三平台 run_app
```

`.venv`、模型、下載套件、API key、歷史日誌均不會預先打包。新增任務只需加入同層資料夾；不重新打包或安裝底座。未放入任務包時，底座也能啟動並列出空清單。

根目錄 `.venv` 僅裝底座。大型依賴在 `share_states/environments/<依賴與平台雜湊>/.venv` 動態建立，避免不同工作流互相破壞版本。相同鎖定檔、Python 與平台可共用相同環境；模型依資源內容雜湊共用。這些都是底座管理的內部資料，流程不需要自帶安裝腳本。


## 流程也有一鍵入口（0.2.4）

在「課程影片產生字幕」或任何流程資料夾內，Windows 開啟 run_app.bat、macOS 開啟 run_app.command、Linux 執行 run_app.sh。入口以自己的位置定位流程，不寫死 ai_flow01、資料夾名稱或磁碟路徑。第一次自動委派同層 flow_base 安裝底座、準備該流程的預設依賴及模型，驗證成功後直接進入該流程。之後沿用可用環境及資源。

學員可以直接輸入 `/input D:\課程\第一堂.mp4`，看到計畫後輸入 `/run`。準備階段不開始影片處理，也不呼叫 LLM。共用底座入口仍提供全部流程的選擇。

流程包需與 **flow_base 0.2.4 或以上**放在同一層。沒有底座包時，入口會提示正確放置方式；目前未設置公開的底座發行網址，因此不會從猜測的網址下載底座。底座包已放好但尚未安裝 uv、Python 或 .venv，則可由流程入口自動完成安裝。

流程入口支援 `--prepare-only`、`--offline`、`--config 設定.json`。例如：

```sh
sh './課程影片產生字幕/run_app.sh' --prepare-only
sh './課程影片產生字幕/run_app.sh' --offline
```

--offline 限制流程資源安裝與執行；若連底座首次安裝也要禁用網路，另設定 UV_OFFLINE=1。準備失敗會記錄在 share_states/launcher 與 share_logs/launcher，重新開啟即可重試。準備資源不產生 run_id，實際執行才產生。

資料夾可以改成 `ai_flow_ASR_video`、`我的字幕工具` 或含空格的名稱，不必修改 wheel 或 flow.json；掃描以同層 flow.json 為準。顯示名稱來自 title；命令的 --task 接受顯示名稱、資料夾名稱或穩定識別碼。名稱重複時會要求唯一選項，兩個資料夾含相同內部 id 時會報錯，不默默合併執行歷史。

新流程作者可提供 module 並省略 id，首次發現時會將隨機識別碼寫入 flow.json（因此檔案需可寫）；之後更新或分享同一流程，應保留產生的 id，不要重新用缺少 id 的模板覆蓋。flow_id 由工作區自動登錄，搬移並保留 share_states 可延續；只分享乾淨流程包到另一個工作區會取得該工作區自己的 flow_id。舊包保留 ai_flow01 作為內部識別碼只是向後相容，不限制學員使用的名稱。

流程的三個薄入口由同一個 scripts/project_launcher.py --flow-task-package 產生；作者維護自己的 manifest 與實作即可，不另寫環境安裝器。

## 進階命令列入口

一般學員使用上方的 run_app 一鍵入口即可；下列命令保留給維護、診斷與自動化串接。

macOS、Linux 使用 POSIX shell，不需要 PowerShell，也不必先 chmod：

```sh
sh ./flow_base/start.sh
```

Windows PowerShell：

```powershell
.\flow_base\start.ps1
```

若 Windows 的本機政策阻擋下載腳本，先檢閱腳本並依組織規則解除該檔案封鎖；啟動器不會修改全域執行政策。

兩個入口均自動定位父資料夾，不依賴目前工作目錄。流程：檢查 uv → 必要時下載固定版本並驗證 SHA-256 → 選用或取得 Python 3.12 → 現場建立 `.venv` → 安裝固定版本底座依賴 → 啟動同一個 `flow_base`。

第一次需網路。已準備完成的工作區可用離線模式；首次建立底座也要完全禁止網路時設定 `UV_OFFLINE=1`，此時缺少快取會明確失敗。macOS/Linux 首次自動取得 uv 需要 curl 或 wget，以及 unzip；已裝 uv 時不需這些下載工具。其他 Unix 若已有相容 uv/Python 可走共用 shell 入口，未提供 BSD 等系統的自動下載組合或驗證保證。

啟動器不使用系統 Python 的全域 pip，不要求管理員權限。每次啟動都以實際 `.venv` Python 檢查版本、虛擬環境位置與底座匯入，不只相信安裝記號。版本錯誤、缺少底座或更新時，會將舊虛擬環境檔案保留在 `share_states/retired_envs`，再建立 Python 3.12 環境；備份是檔案保留，不保證搬移後的舊 venv 能直接啟動。`.venv` 若不是可辨識的虛擬環境或是符號連結，會停止，避免處理不相關資料。中斷安裝後可重跑；若程序被強制終止留下 `share_states/bootstrap.lock`，確認已無安裝程序後移除該空鎖定資料夾再重跑。

若看見 `Package 'flow-base' requires a different Python: 3.10.10`，表示安裝程序使用了不支援的 Python 3.10。pip 的升級通知與此無關。學員請執行 `start.ps1` 或 `start.sh`，讓啟動器選擇／取得 Python 3.12，不需先用系統 `pip install` 安裝 wheel。更新至 0.2.1 只需更換 `flow_base` 資料夾，保留各流程資料夾、`share_states` 與 `share_logs`。

## 共用入口

以下以 shell 為例，Windows 將前面的 `sh ./flow_base/start.sh` 換為 `.\flow_base\start.ps1`。

```sh
sh ./flow_base/start.sh list
sh ./flow_base/start.sh inspect
sh ./flow_base/start.sh plan --task ai_flow01 --input "課程.mp4"
sh ./flow_base/start.sh prepare --task ai_flow01
sh ./flow_base/start.sh verify --task ai_flow01
sh ./flow_base/start.sh run --task ai_flow01 --input "課程.mp4"
sh ./flow_base/start.sh history --task ai_flow01
```

輸入／輸出相對路徑以工作區根目錄為準。`--config`、`--intent` 檔案的相對路徑以命令當下工作目錄為準，建議給絕對路徑。所有外部程序使用 argv 陣列，不將自然語言拼入 shell。

## flow_id、run_id、job_id

| 欄位 | 建立與用途 | 重跑／更新／搬移 |
| --- | --- | --- |
| 內部定義 ID | flow.json 的 id，與資料夾／顯示名稱無關；新流程可省略並在首次登錄產生 | 保留此值可延續歷史；學員不必記憶或修改 |
| flow_id | 首次掃描自動產生 UUID，登錄在 share_states/state.sqlite3 | 同一工作區中的同一任務沿用；更新版本與整個工作區搬移不改變 |
| run_id | 每次實際執行自動產生 UUID | 每次不同，命中快取也不同；成功、可捕捉失敗、中斷均記錄 |
| job_id | 由 flow_id、輸入內容與輸出位置計算 | 同一工作可續跑與重用 LLM 帳本，不當成單次執行識別碼 |

`list`／`catalog` 會自動登錄流程，但不匯入任務程式、不執行流程。`plan` 不建立 run_id。`run`、自然語言的實際執行、`dispatch --execute` 與 `demo` 都建立 run_id；前提是任務與設定已能載入。執行中的輸入遺失、空間不足與輸出衝突也會記錄失敗的 run_id。錯誤回傳包含可追查的 ID。

使用 `--flow-id flow_...` 可替代 `--task ai_flow01`。`history` 可查全部或單一流程的紀錄。每次日誌保存在 `share_logs/<flow_id>/<run_id>/`，原始輸出報告含三種識別碼。流程更新保留 flow_id，各次快照記錄實際版本與套件雜湊。

作業系統斷電或 SIGKILL 無法由 Python 捕捉，最後紀錄可能停在 starting/preparing/running；它不代表成功。重新執行會取得新 run_id，仍可沿用先前完成的內容快取。複製工作區前應先停止執行中的流程，避免搬移尚在寫入的 SQLite 資料。

## 自然語言

預設無參數啟動互動入口；非互動環境則列流程。三種模式共用相同驗證與執行器：

1. **離線常用語句模式**：不花 token，以任務宣告的關鍵字和規則比對。支援常見字幕、語言、CPU/GPU、輸入與輸出要求；不宣稱完整語意理解。複雜意圖應用下列兩種模式。
2. **宿主 Agent 模式**：`catalog` 輸出已安裝能力、參數白名單及 Intent Schema，宿主理解需求後用 `dispatch` 傳入 JSON；底座不再額外呼叫路由模型。這是 CLI 整合契約，並未自動安裝 ChatGPT/Codex 插件或 MCP server。
3. **獨立 LLM 語意模式**：明確設定 provider 後用 `ask --router llm` 或聊天內 `/router llm`；每次新意圖至多一次模型呼叫，結果快取，不產生程式碼或命令。

```sh
sh ./flow_base/start.sh ask '把「課程.mp4」做成繁體中文字幕'
sh ./flow_base/start.sh ask '把「課程.mp4」做成繁體中文字幕' --execute
sh ./flow_base/start.sh ask '複製「講義.txt」' --execute
sh ./flow_base/start.sh catalog
sh ./flow_base/start.sh dispatch --intent intent.json --execute
```

`ask` 預設只產生計畫；`--execute` 才執行。互動模式可用 `/input 路徑`、`/output 路徑` 補充資料，輸入 `/run` 執行計畫。缺少重要參數、流程衝突、否定任務或未核對的 LLM 設定會停止並回傳原因。

宿主 Intent 範例：

```json
{
  "task": "ai_flow01",
  "input": "課程.mp4",
  "output": null,
  "overrides": {"options": {"language": "zh", "traditional_chinese": true}},
  "questions": []
}
```

只有 `flow.json` 的 `parameter_paths` 允許的參數可由意圖輸入。LLM 不能設定安裝來源、任意 Python 專案、shell 命令或新增任務。新任務包含可執行 Python 程式，只應使用可信來源；SHA-256 提供完整性檢查，不等同發布者簽章。

## LLM 設定與用量

`share_states/settings/router.json` 是共用語意路由設定，格式見下方。範例單價只是展示，不能直接視為真實報價；填入實際模型、已核對單價與依據後，才能將 `enabled`、`pricing_verified` 設為 true。密鑰只放環境變數，不放 JSON 或任務包。

```json
{
  "enabled": false,
  "endpoint": "https://api.openai.com/v1/chat/completions",
  "model": "",
  "api_key_env": "FLOW_BASE_API_KEY",
  "budget_usd": 0.02,
  "input_usd_per_million": 0,
  "output_usd_per_million": 0,
  "pricing_verified": false,
  "pricing_note": "",
  "max_output_tokens": 1200,
  "max_calls": 1
}
```

這是供允許 LLM 的流程選用之通用能力。流程可在 manifest 宣告 llm_policy=forbidden；底座會拒絕該流程的外部語意路由。工作區含禁止 LLM 的流程且尚未選定流程時，也拒絕外部路由，須先選定允許的流程。字幕流程 0.3.0 宣告 forbidden，沒有自動 LLM 校訂。路由每個新意圖一個帳本；其他流程可按穩定 job_id 使用通用帳本，不因重跑換 run_id 而重設。每次外部呼叫前用 SQLite 原子保留預估費用，限制次數及最大輸出 token；回應後按 usage 結算。網路結果不明時保留額度且不自動重送；同一結果可重用。修改輸出位置會形成另一個 job 與預算範圍。

目前費用保留以 UTF-8 位元組與額外餘裕估 token，是保守估計，**不是供應商帳單的硬性保證**。要絕對限制實付，需搭配供應商帳號的支出限制。未測試真實付費 LLM 呼叫；單價由使用者核對，底座不自動猜測或換算幣別。

## 搬移與更新

停止全部流程後，複製 `flow_base`、各流程資料夾、`share_states` 與需要保留的 `share_logs`。`.venv` 不需複製；若一起搬移，啟動器偵測根路徑／平台不同會重建它。任務隔離環境也記錄位置，搬移後於 prepare 階段重建。Windows/macOS/Linux 之間不直接沿用二進位環境。

`share_states` 中模型會驗證大小與 SHA-256 後沿用，套件快取可復用相容內容。可移除可重建的 `environments`、`packages`、`python`、`plugins` 節省傳輸；保留 state.sqlite3、jobs、artifacts、resources、settings 以延續 ID、快取與費用帳本。不要只複製資料庫而遺漏需要的 artifacts；遺失的快取會重新計算。日誌、逐字稿與設定可能含私人內容，分享學員時使用乾淨發行 ZIP。

更新流程只更換相應流程包並重啟入口；`flow_id` 保留，變動的任務程式雜湊會令舊計算快取失效。更新底座只更換 `flow_base` 資料夾，啟動器依 wheel SHA 更新 `.venv`，共用狀態仍保留。

## 完整原始碼與擴充

每包的 `source.zip` 是完整原始碼；底座原始碼包不夾帶任務實作。將各 source.zip 解到同一個開發目錄，可得到 base/、ai_flow01/、ai_flow02/、tools/、tests/。

```sh
uv sync --project base --group dev --locked
python tools/release.py
uv run --project base --group dev pytest tests ai_flow01/tests -q
```

`tools/release.py` 只建立存在的專案，底座可獨立打包。完整整合測試需同時解開兩個範例任務原始碼並先執行 release。測試要驗證不同流程，不需要把任務實作放回底座。

任務 wheel 只包含自己的 Python namespace 與同名 distribution metadata，入口是 `<module>.adapter.Adapter`。module 是套件內的技術名稱，與資料夾名稱及 id 無關；舊 manifest 沒有 module 時沿用 id。繼承 `flow_base.engine.Adapter`，提供 typed configuration 與 handlers；視需求覆寫 plan、prepare、verify、pre_run、inspect_input、step_settings、profile_key、minimum_space、job_scoped、publish。步驟接收 Context、Step、依賴檔案與目標檔案，必須回傳目標目錄內的產物。Context 提供 flow_id、run_id、job_id、logdir 及底座服務。

任務只宣告差異：能力描述、可接受參數、專屬步驟、依賴 lock、資源 hash、校訂規則。環境建立、下載校驗、ID 登錄、記錄、鎖定與費用保留留在底座。

## 已知驗證邊界

Linux x64 已實測 shell 首次安裝、無現成 uv/Python 3.12 的動態取得、真實影片抽音訊與 VAD/Whisper/SRT、重跑快取、ID 登錄與多流程隔離。Windows/macOS 入口已提供但未在真機執行，CUDA 是模擬失敗回退測試而非實體 GPU 驗證。字幕 sample 不是中文準確率基準。未實測付費 LLM，尚不宣稱所有平台 production-ready。

## 0.2.5 通用流程能力契約

依賴方向只有「流程 → flow_base API」。底座不 import 任一流程，不識別模型名稱、字幕格式、QA 規則或人工核准語意；新增流程不修改底座。流程的 pyproject/lock、資源目錄、參數模型、handlers 與特殊操作都跟流程包獨立發布。

manifest 的 `actions` 宣告可用操作名稱；通用命令為：

```sh
sh flow_base/start.sh action --task "流程顯示名稱" --name "流程宣告的操作" --params parameters.json
```

底座僅檢查宣告白名單，再呼叫 `Adapter.invoke_action(runtime, name, parameters)`。參數驗證、輸入輸出及領域狀態由流程負責。每次能力呼叫另登錄 run_id 與成功／失敗日誌；執行成功僅代表操作完成，領域結果保留在 result 中。

選用擴充：`describe(workflow)` 提供流程說明；`check_output(runtime, workflow, output)` 在覆寫報告前檢查領域限制；`artifact_valid(runtime, handler, artifact)` 驗證流程的附屬快取。未實作這些擴充的舊流程照常使用既有 API 0.2。底座不解讀流程自訂 QA 狀態。

base wheel 只有輕量底座依賴；安裝底座不安裝 ASR。字幕、複製範例各有自己的 wheel、manifest、source.zip，任何一包都可單獨更新或移除。原始碼中的跨包整合測試不屬於 runtime 依賴。

## 0.2.6 環境、進度與用量契約

底座用通用探測提供版本化環境快照；支援 nvidia-smi（包含 Windows 常見位置）及有逾時限制的獨立 CUDA 驅動 API 探測，不需 PyTorch、ONNX 或任務依賴。GPU 狀態不等同任務後端驗證。Task.configuration 會把快照交給選用的 Adapter.resolve_environment(workflow, environment)，由流程回傳具體執行設定；沒有此方法的舊流程仍可運作。底座不寫死模型或任務的 CPU／CUDA 策略。

run 的預設 stderr 為醒目繁體中文生命週期訊息，stdout 仍保留 JSON 結果。FLOW_BASE_OUTPUT=json 可切成 JSON 事件，quiet 可關閉底座進度顯示。狀態檔仍會保存。流程可提供 step_title(step) 與 result_summary(report)，讓底座顯示領域名稱及驗收狀態，不需底座識別領域代碼。

長任務 worker 可在自己的 log 寫一行 JSON：{"event":"progress","completed":3,"total":10,"unit":"項目","label":"處理中"}。底座僅轉送合法進度，不把一般日誌、逐字稿或模型回覆轉印到畫面。有總量時顯示真實比例；未知總量時顯示活動與已耗時。尚未完成區段的內部進度未知，不捏造百分比或 ETA。事件存 events.jsonl，最新狀態存 live_progress.json，均按 flow_id／run_id 隔離。

Token 用量以本次 run 期間、經底座登錄的 provider usage 為準。Ledger.reserve／settle 自動記錄，其他流程可用 runtime.record_token_usage(request_id, pending=True) 登錄請求，再以 runtime.record_token_usage(request_id, response_usage) 記錄收據。不要傳入提示詞或完整回覆。相同 request_id 重複結算不重複加總；未回報的用量是 null，不能視為零；已知部分另存 known_subtotals。

支援 prompt_tokens／completion_tokens 與 input_tokens／output_tokens 格式，cached_tokens 取對應 details 欄位；未快取輸入=總輸入−快取命中輸入，可能包含供應商另計價的快取寫入，不代表精確帳單。輸出 token 不重複加計其已包含的 reasoning tokens。未呼叫 API、重用本機節點結果，該次新增 API token 為零。

統計範圍不包含宿主 Codex 對話、run 開始前的語意路由或未登錄之外部呼叫。0.2.10 起宿主用量另列 host_agent_usage，依明確綁定的本機收據或轉送通知顯示；沒有來源時仍為無法取得，不把 0 當成未知值。

硬體偵測及輸出格式已做自動測試；實體 Windows CUDA、macOS 與宿主對話自動轉述仍需實機驗收。原生沙盒的共用路徑授權不由 AGENTS.md 或本次功能繞過。


## 固定產物目錄（0.2.7 起）

每個流程保有自己的 `outputs/`。`plan`、`run`、自然語言與 dispatch 不指定輸出時，統一產生 `流程資料夾/outputs/job-輸入名稱-穩定識別碼/`。規劃結果會列出實際路徑；不用讓 Agent 自行發明另一個資料夾。識別碼以輸入路徑產生，同一輸入位置重跑沿用成果區，流程內的路徑以流程為基準，因此流程資料夾改名仍可沿用；若原檔內容已改變，舊成果不會被覆寫，請明確選新的 outputs 子目錄。這個資料夾識別碼不是 flow_id、run_id 或 job_id。

| 區域 | 用途 |
| --- | --- |
| `outputs/<工作>/docs/` | 執行摘要、交付說明及任務生成文件 |
| `outputs/<工作>/artifacts/` | 實際成果與不可拆散的領域 bundle |
| `outputs/<工作>/scripts/` | 任務實際交付的腳本；按需建立，不自動執行 |
| `outputs/<工作>/report.json` | 狀態、版本、來源與執行識別碼 |
| `outputs/<工作>/deliverables.json` | 相對路徑、類別、大小及 SHA-256 清單 |
| `share_states/` | 環境、模型、步驟快取與續跑狀態 |
| `share_logs/<flow_id>/<run_id>/` | 每次執行的日誌、事件與進度 |

同一工作的新 run_id 留在共用執行歷史，不複製整套成果。`docs/run-summary.md` 與交付清單反映最近一次成功發布／審閱後的狀態，當次執行真相以根層 report.json 及 run 日誌為準。舊人工修訂的保護由流程負責，底座不判定領域核准。

`--output` 和 `/output` 保留明確另存能力，相對路徑仍以共用工作區為準；宿主只有收到明確位置要求才覆寫預設。底座拒絕輸出到其他流程、流程程式區、共用狀態／日誌／環境及符號連結。既有 `share_states/outputs` 不會自動搬移或刪除，舊人審操作仍可依原位置開啟；新辨識結果使用流程 outputs。

流程以 `flow.json` 的 `output_layout: "categorized-v1"` 宣告新契約。未宣告的舊流程沿用 flat-v1；已有平面 report 的成果重跑保持原格式，不擅自搬動。新版契約需要底座 0.2.7 以上。

開發者沿用 `publish(terminal, artifacts_dir, workflow)` 即可輸出一般成果；需要多類交付時，覆寫 `publish_outputs(terminal, output_root, workflow)`，分別寫入 docs、artifacts、scripts。底座保留 `report.json`、`deliverables.json` 與 `docs/run-summary.md`；流程不要覆寫。輸出程式只能寫分配的區域，仍是受信任的 Python 外掛，路徑檢查不等於作業系統沙盒。

流程程式碼與安裝腳本留在套件及 source.zip；outputs/scripts 是此次任務交付物，兩者不可混用。更新時保留 outputs；分享軟體預設排除 outputs、share_states、share_logs、模型及環境。要分享成果時，另外選取特定工作資料夾，確認內容後再分享。


## 失敗能力入口（0.2.8）

流程可以在 flow.json 宣告 recovery_action（必須同時存在於 actions）。共用 launcher 會生成 recover_failed_run.bat／.command／.sh，委派原本 run_app 與同層底座，沒有第二套安裝器。launch --action <已宣告能力> --params <選用 JSON> 直接執行該能力，不先準備或載入領域模型。底座只提供 action_context 的 flow_id、run_id、流程位置及 logdir；是否能恢復、來源驗證及處理方式由流程實作。

一般工作失敗時 run.json 會同步標為 failed／interrupted；pending_step.json 保存最新待執行節點的產物位置供診斷。恢復操作有自己的 run_id，不將原失敗歷史改成成功。
