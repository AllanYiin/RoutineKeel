# flow_base：宿主 Agent 執行契約

## 定位與範圍

這是已封裝的工作流程工作區。使用者要求執行工作時，宿主 Agent 負責理解需求、選擇已登錄流程、呼叫既有 CLI、讀取有限結果。實際步驟由流程包執行。除非使用者要求開發，不另寫替代腳本或改動 wheel。

預設每個流程資料夾各自作為 Codex 專案／repo，對話由宿主管理，各流程不需要合併成一個專案。共用執行工作區是這些流程的父資料夾，與對話專案根是不同概念。從目前流程呼叫旁邊的底座；僅存取必要的 ../flow_base、../.venv、../share_states、../share_logs 及使用者指定輸入／輸出。catalog 由底座讀取同層流程的宣告資料，不授權宿主 Agent 掃描其他流程內容。若環境限制阻擋，明確回報受阻的共用路徑，由使用者依其宿主可用機制允許必要存取；不得假定 AGENTS.md 能突破沙盒，也不一律要求改用共同父目錄作為所有對話的專案。

先讀本文件、目標流程的 AGENTS.md、flow.json 與 README.md；settings.json 如存在也應讀取。不要自動解開 source.zip 或檢查整個系統的 Python／模型快取。環境準備由底座執行。

## 確定性入口

以啟動器所在位置定位工作區，不以當前 shell 目錄猜測。不直接使用系統 python、pip 或額外付費 LLM 來替代流程。

Windows PowerShell（先根據實際專案根與已選流程設定變數；以下路徑僅為可替換示例）：

```powershell
$Workspace = (Resolve-Path -LiteralPath 'D:\flow_workspace').Path
$TaskDir = Get-Item -LiteralPath (Join-Path $Workspace '資料整理流程')
$Launcher = Join-Path $Workspace 'flow_base\start.ps1'
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Launcher catalog
if ($LASTEXITCODE -ne 0) { throw 'flow_base catalog failed' }
```

取得 catalog 後確認該流程的 folder、title、flow_id、runtime_api 與 actions；不可自行捏造 id。下列 $InputFile 必須來自使用者指定輸入。預設省略 --output，由底座配置目前流程 outputs 的工作資料夾；只有使用者明確要求另存位置才傳入 --output。缺少輸入才詢問，不搜尋其他磁碟替代：

```powershell
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Launcher plan --task $TaskDir.Name --input $InputFile
if ($LASTEXITCODE -ne 0) { throw 'flow_base plan failed' }
# 檢查回傳 plan.blocked。使用者已要求執行且無阻擋時，直接繼續，不重複要求同意。
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Launcher run --task $TaskDir.Name --input $InputFile
if ($LASTEXITCODE -ne 0) { throw 'flow_base run failed; inspect its error and log path' }
```

macOS/Linux 使用同一介面（變數由已確認路徑設定）：

```sh
sh "$workspace/flow_base/start.sh" catalog
sh "$workspace/flow_base/start.sh" plan --task "$task_name" --input "$input_file"
sh "$workspace/flow_base/start.sh" run --task "$task_name" --input "$input_file"
```

每個命令完成後檢查退出碼及 JSON 結果，前一步失敗不得繼續。bootstrap 訊息可能位於結果 JSON 之前，不要假定整段 stdout 都是單一 JSON。明確路徑與參數分開傳遞，不使用 Invoke-Expression／eval，不拼接使用者文字成 shell 程式。JSON 以 UTF-8 寫入。

使用者雙擊 run_app.* 是互動入口；Agent 自動執行使用上述非互動 CLI，不開無人操作的 chat。長工作保留既有程序並查看該 run 日誌，不因工具短暫等待逾時重複啟動同一工作。

## 特殊能力與結果證據

能力僅能從該流程 manifest.actions 選擇，參數格式依該流程文件；透過通用 `action --task <名稱或flow_id> --name <宣告操作> --params <JSON完整路徑>` 呼叫。底座不定義領域核准語意。

執行後讀取本次 output/report.json 及相應 share_logs/<flow_id>/<run_id> 記錄。回報 flow_id、run_id、job_id、步驟 executed／cached、結果狀態及實際產物路徑。結果需對應使用者指定的來源與模型／設定；未執行或只有 demo，不得宣稱正式完成。

既有產物可由引擎按來源及設定指紋重用；不能因檔名相似或看起來是同一課程，就從其他專案複製舊結果並宣稱本次工作完成。若使用者明確要求匯入舊成果，先查可用的匯入能力；沒有就說明限制，不偽造 report／run_id。

失敗時保留日誌、狀態及人工修訂，回報 error.code／message／log，不自行降級模型、替換引擎、繞過領域檢查或改用其他同類技能。不得掃描未授權的外部專案、全磁碟、個人模型快取或其他對話資料。

宿主 Agent 的對話用量與流程 runner 的 API 用量分開：流程回報 llm_calls=0 不代表 Codex 對話沒有 token 成本。遵守流程自己的資料量與外部傳輸規範。

## 讓使用者看得見進度

run 的生命週期事件與進度檔是唯一執行狀態依據。收到開始、節點進入／完成、結束／失敗時，應在對話正文用醒目的簡短訊息轉述流程名稱、節點名稱及結果；不得只把狀態留在折疊工具輸出。若工具執行中無法即時取得新輸出，不捏造進度；持續沿用同一程序，於可讀取時查看該 run 的 live_progress.json。

長任務顯示真實 completed／total 和單位；未知總量只轉述活動與已耗時。回報 token_usage 時區分供應商輸入快取、本機步驟快取及宿主對話。未提供的數值不可填 0。宿主 UI 是否自動展開工具輸出不由本底座控制。

## 任務輸出規範

預設使用目前流程 outputs/<工作>/。不要把成果寫在流程根目錄、底座、共用狀態區或任意臨時目錄。先從 plan.output 取得位置，再由 run 使用同一預設；不能自行把 run_id 當輸出目錄，每次重跑會失去續跑與人工成果關聯。

根層 report.json 是執行報告，deliverables.json 列出分類及相對路徑。docs/ 放工作生成的文件與執行摘要，artifacts/ 放實際成果與領域 bundle；scripts/ 只有實際交付腳本時才建立，不能因存在腳本就自動執行。流程的原始碼／安裝腳本仍在套件中。操作人審等能力時傳整個工作根目錄，不傳 artifacts 子目錄。

Agent 額外產生的交付說明或腳本也應放在該工作 docs/ 或 scripts/，不得改動底座保留的 docs/run-summary.md、report.json 或捏造交付雜湊；交付清單只代表底座最近一次發布時已登錄的檔案。修改成果後應走流程自己的重建／驗收能力刷新索引，不能僅改狀態為 complete。流程更新與發行不得刪除或夾帶 outputs。


## 0.2.10 醒目顯示與 Session 綁定

- 開始、節點進入、節點完成與終止，都從本次 run 的事件或 live_status.md 取得證據，於對話正文使用粗體標示，例如「▶ FLOW_BASE 已啟動｜流程名稱」「✓ 節點 2/5 完成｜名稱」「■ FLOW_BASE 已結束｜待真人確認」。尚未收到開始事件時只說正在啟動。不要只把橫幅藏在工具輸出，也不要編造完成百分比。
- 特殊 action 也有完整生命週期；執行結束不等同領域驗收完成，result.decision／status 必須如實保留。
- stdout result.presentation 提供 console.log、live_status.md 與事件路徑；不要為查看進度重新啟動同一工作。結束摘要中的多行交付連結要完整呈現。
- 若宿主已提供 CODEX_THREAD_ID，讓啟動器正常繼承，不改寫為其他視窗的 ID。若沒有，僅在已確認目前 Session 檔案或 Thread ID 時傳入 FLOW_BASE_CODEX_SESSION_FILE／FLOW_BASE_CODEX_THREAD_ID；不要用 --latest 推定本次身分，不讀取其他對話內容。
- 結束時優先顯示 token_usage.host_agent_usage 的對話累計與 run_delta，分列已快取／未快取輸入及輸出，附來源與「截至已收到收據」。run_delta 為 null 就說無法計算；no_new_usage 必須說尚未收到新增用量，不能稱本輪免費。結束後的這則回覆尚未計入。
- registered_workflow_api 維持獨立範圍；不能與 Codex 用量相加。沒有宿主來源時明示無法取得，不因此中斷已授權的工作流程。自訂 App Server 宿主可轉送 tokenUsage 通知到專用 JSONL；不能說已自動訂閱既有視窗，除非實際持有該連線。
