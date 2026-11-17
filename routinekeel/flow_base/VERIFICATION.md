# 驗證紀錄

## Windows 啟動器的 uv Python 版本連結修正（2026-11-17）

以 Linux PowerShell 7.6.10 執行啟動器和流程環境回歸，共 **18 passed**。啟動器新增五種情境，涵蓋一般啟動、連結錯誤重試成功並在下次重建時沿用本機目錄、備援也失敗、缺少 `LOCALAPPDATA`，以及不屬於目錄連結的下載錯誤。測試用 uv 替身寫入 stderr 並回傳非零退出碼，同時核對啟動狀態、原始錯誤、嘗試路徑及既有虛擬環境備份。流程環境的三項測試核對各平台傳給 uv 的目錄，以及環境就緒後寫入的 `location.json`。Windows PowerShell 5.1 雙擊及實際失敗磁碟仍待實機驗證。

`share_states/launcher/use-local-python` 記錄工作區已驗證的備援選擇。若工作區磁碟已支援 uv 需要的連結，移除該標記即可讓後續環境建置回到工作區的 `share_states/python`。既有虛擬環境透過各自的 `pyvenv.cfg` 繼續連到建立時所用的 Python。

## 0.2.10 生命週期及 Codex 收據（2026-11-16）

新增 22 個案例涵蓋新／舊 rollout、App Server 通知、累計去重、明確 Session 綁定、多視窗拒絕誤認、缺失欄位、半行續讀、壞行、來源／計數重設、終止快照固定、CLI 查詢、三種輸出模式與 action 成功／失敗生命週期。針對性測試連同既有進度用量案例共 **35 passed**。

封裝後完整回歸 **221 passed，0 skipped**。命令：`FLOW_BASE_TEST_PWSH=/path/to/pwsh uv run --project base --group dev pytest tests ai_flow01/tests -q`。嚴格 APSM 檢查通過。

另以已建置的 flow_base-0.2.10 wheel 實際執行獨立複製流程，核對 runtime 版本、四種橫幅、stdout JSON、console.log／live_status.md、實際複製成果與 Session 統計。合成 Thread 累計 135,000、流程期間沒有新收據，正確標示 no_new_usage，而非宣稱真實對話免費。

通知結構以本機 Codex CLI 0.167.0-alpha.4 的 generate-json-schema 核對；統計測試採合成收據，沒有讀取其他視窗的私人對話，也沒有把示例數字當作使用者實際耗用。此環境未驗證 Windows PowerShell 5.1／macOS 真機或使用者目前 Codex 視窗的實際收據連線；Linux PowerShell 7.6.0 的啟動器與日誌回歸包含於測試。

此版只發行 flow_base 0.2.10，字幕流程維持 0.3.4。原流程結果、模型、ASR 快取與人工核准資料不進包、不重跑。

## 0.2.9 底座日誌修正（2026-11-16）

完整測試 **184 passed，0 skipped**。命令：`FLOW_BASE_TEST_PWSH=/path/to/pwsh uv run --project base --group dev pytest tests ai_flow01/tests -q`。PowerShell 7.6.0 Linux 測試 runtime 由官方發行下載並核對官方 SHA-256；它僅用於開發測試，不包含於發行包，也不改變 Windows 使用內建 PowerShell 的方式。

新增驗證：實際執行 production PowerShell 命令區段，stdout／stderr 與中文均在 transcript；正常命令即使寫 stderr 仍為成功，退出碼 0／2／130 正確保留；互動聊天標記未寫入 transcript。原先六項 PowerShell 測試亦通過，包括多個 uv 候選、語法與 bootstrap 失敗保護。

結構化診斷使用明確的合成 Pydantic 案例 `invalid word timeline`、id 20，驗證原因／資料列 id／堆疊、因果鏈、已知金鑰遮罩、驗證輸入排除、相同 error_id 與 CLI→run 關聯；通用引擎失敗後 failure／report／events 的證據一致，run 狀態為 failed。沒有將合成案例當成使用者實際失敗段落，也沒有修改舊 run 或重跑 ASR。

Windows PowerShell 5.1 真機、Windows CMD 雙擊、macOS 真機與實體 CUDA 本輪仍未驗證。此次只發行底座 0.2.9，字幕流程維持 0.3.3。

## 0.2.8 底座／0.3.3 字幕流程（2026-11-16）

完整測試 **169 passed、6 skipped**；六項 PowerShell 測試未提供 runtime。新增測試涵蓋超界、倒序、重疊、零長度字級時間的保守處理；段落非法仍阻擋；恢復核對 checkpoint、ASR 組裝、模型、原影片與音訊、人工修訂保護；通用 action context 清理與狀態持久化；薄恢復入口參數／退出碼及跳過模型準備。

真實處理環境驗證：在先前公開英文短片 ASR 的副本中注入明確標示的合成字級異常，保留原資料，從六個 0.3.2 checkpoint 恢復。使用實際隔離環境執行 qa_bundle，守衛只允許這個 worker action；ASR／模型載入呼叫為零。SRT、VTT、QA、疑義原音均產出，狀態 REVIEW_REQUIRED，原失敗 run 保留，恢復另有 run_id。這不是使用者 391 區段的復原結果；附件未包含原 ASR 或 checkpoint，不能宣稱已替使用者產出 SRT。

本輪沒有 Windows／macOS 真機或實體 CUDA 測試，不將合成異常視為使用者第 20 段的精確根因。恢復只接受已知相容 worker 指紋與完整來源校驗，不會為湊出字幕猜測來源或自動重辨識。

## 0.2.7 底座／0.3.2 字幕流程（2026-11-16）

完整測試 **150 passed、6 skipped**。六項 PowerShell 測試因未提供 runtime 而跳過。新增覆蓋：CLI／自然語言一致的流程內預設產物區、工作區搬移、流程資料夾改名與快取、明確另存與受保護路徑、符號連結拒絕、輸入變更不覆寫舊工作、舊 flat-v1 結果保持原位、跨類別 docs/artifacts/scripts 交付及不自動執行腳本、新舊字幕 review-packet 與分類 review-build、原始碼封裝排除私人 outputs／狀態／日誌。

分類交付由底座提供，字幕審閱 bundle 由流程保有。清單使用相對路徑及每檔 SHA-256，根層報告與共享 run 日誌仍為執行證據。沒有新增自動 LLM 呼叫。Windows／macOS 真機、實體 CUDA 尚未驗證。

實際短片：large-v3 CPU 完成五節點，新產物位於流程 outputs 的 docs／artifacts；同工作再跑五步皆命中快取。review-packet 正確定位新格式，review-build 實際重建後仍 REVIEW_REQUIRED 並刷新索引。加入明確標為合成 fixture 的空 edits.json 後，一般 run 以 HUMAN_REVIEW_PRESENT 拒絕覆寫，原檔保留；未偽造聽審核准。

## 0.2.6 底座／0.3.1 字幕流程（2026-11-16）

本輪新增底座硬體快照、流程 auto 選擇鉤子、醒目生命週期、真實長任務進度及 provider token 收據統計。GPU 情境以明確 mock 測試；本環境實際為 Linux CPU，沒有宣稱 Windows GPU 驗證成功。

完整測試命令 `uv run --project base --group dev pytest tests ai_flow01/tests -q`：**130 passed、6 skipped**。跳過的六項需要 PowerShell runtime；未計為通過。實際約 29 秒公開英文樣本影片使用 large-v3 CPU 完成五個節點，辨識進度從 0／6 到 6／6 區段，重跑五步均命中快取、run_id 不同，結果維持 REVIEW_REQUIRED。此本機流程未呼叫 LLM API，三類 API token 均為零，宿主對話用量維持 unavailable。

測試涵蓋 CUDA 可見／缺少 nvidia-smi／偵測失敗／明確停用、CPU 或 CUDA 設定優先、profile 衝突、未知狀態不靜默選 CPU、動態硬體資訊不污染 ASR 快取；通用事件順序、失敗不印完成、未知百分比、真實子程序進度與逾時、stdout JSON 不被人類橫幅破壞；provider 快取／未快取輸入、輸出、缺失收據、重複結算與 Ledger 自動登錄。Token 測試都是合成回應，不是付費呼叫。

流程 API token 與宿主 Codex 對話用量分開：前者以本次 run 內經底座登錄的 provider usage 統計，後者未暴露給底座，顯示 unavailable。沒有用模擬數字宣稱宿主實際帳務。

發行外層已包含工作區、底座與流程各自的 AGENTS.md。每個流程仍各自建立對話專案；必要共用路徑授權仍取決於宿主環境，指引不繞過沙盒。


## 0.2.5 底座／0.3.0 字幕流程（2026-11-16）

本輪環境 Linux x86_64、Python 3.12。命令 `uv run --project base --group dev pytest tests ai_flow01/tests -q`：**95 passed、6 skipped**。6 項是未提供 PowerShell runtime 的平台測試，不計為通過。本輪沒有 Windows／macOS 真機或實體 CUDA 驗證。

新增驗證：

- base wheel 不含任務 namespace、ASR 依賴、模型名稱或字幕能力分支；移除字幕包後檔案複製仍可執行，且不建立 ASR 環境。
- manifest 能力白名單、通用 action 每次 run_id／日誌、流程禁止 LLM 時在傳輸前阻擋；一般流程保留獨立路由能力。
- 固定 large-v3 五個模型檔案與來源 revision；CPU/CUDA 鎖 setuptools 80.9.0，下載模型前驗證 imports；worker 錯誤包含有限日誌尾端。
- VAD 保存、區段 ASR 中斷後續跑、原片偏移、GPU 不靜默換模型、資源不足明確停止。
- QA 無警告仍待聽審、有硬性錯誤不能核准；真人確認必填、改字令核准失效、舊字幕與疑義表備份、CSV 人工註記保留、一般 run 不覆蓋人工修訂、WAV 主產物與 JSON 附屬檔快取分開驗證。
- 有限疑義 packet 上限及遮罩；本機 runner 零 API／token。PASS 路徑只用明確標示的合成 fixture，沒有核准真實課程內容。

實際推論：完整下載並驗證約 3.09 GB large-v3 model.bin 與配套檔案，CPU INT8 載入、編碼解碼及 Silero VAD 成功。使用 PyAV 組合的約 29 秒 MP4（兩段公開英文 JFK 語音，中間靜音），完成五步管線並輸出 SRT/VTT、原音疑義與 QA，保持 REVIEW_REQUIRED。另取使用者提供音訊 60–78 秒的 18 秒片段，本機 large-v3 產生 6 個字幕 cue、0 硬性 QA／0 疑義，仍 REVIEW_REQUIRED。測試音訊與逐字稿不打包，也未送至外部 LLM。

較早的整段 vad_filter 測試不作為新版逐區時間軸驗收；新版已改為先保存 VAD、逐區辨識並加回原片偏移。短片測試不是整份課程準確率或 WER/CER 基準；課程特定 DTW 強制對齊沒有納入。

發行使用 `python tools/release.py`，通過專案 CLI profile strict validator；獨立 ZIP 及集合 ZIP 含完整 source.zip，不含 .venv、已下載模型、測試課程、share_states 或日誌。發行內容可拆為一份底座與各自流程包。shell 入口另以 `sh -n` 檢查。

舊版紀錄如下，僅為歷史證據；舊 tiny／付費校訂設計不代表本版字幕流程行為。

## 0.2.4 流程入口與名稱解耦（2026-11-15）

完整測試 75 項通過，包含原有 PowerShell 回歸及新流程入口測試。已驗證中文／空格資料夾、既有 ai_flow01 包直接改名為 ai_flow_ASR_video、新流程自動持久化 id、併發登錄、重複識別碼拒絕、顯示名稱歧義拒絕、module 與 id 解耦、改名後保留 flow_id/job_id/歷史與步驟快取。

流程的三平台薄入口由 project_launcher.py --flow-task-package 生成。已驗證 POSIX 入口從不同 cwd 定位共用底座、原樣傳遞含空格參數、保留錯誤退出碼、無底座時提供放置提示。CLI launch 支援先準備後進入指定流程、直接 /input、受限自然語言參數、可信 JSON 設定、安裝失敗紀錄與重試，不因安裝建立 run_id。

真實乾淨安裝：解壓新版底座與檔案複製流程至含中文及空格的目錄，PATH 僅提供 shell／下載／解壓工具，沒有 uv、Python、python3 或 pip。從流程自己的 run_app.sh --prepare-only 啟動，成功下載驗證 uv 0.13.6、下載 Python 3.12.16、建立共用 .venv、安裝底座 0.2.4，完成指定流程準備。再次 --prepare-only --offline 成功，pyvenv.cfg 時間戳不變，未重建環境。

真實字幕流程入口：從 dist/workspace/ai_flow01/run_app.sh --prepare-only --offline 啟動，成功重用先前校驗的 Whisper tiny 模型與 CPU 依賴，verify 回報 faster-whisper 1.2.1、CTranslate2 4.6.0、PyAV 16.0.1、ONNX Runtime 1.23.2、device=cpu。此輪重點是入口及安裝調度，未重跑影片準確率評測，也未將既有模型快取描述為首次 HF 下載。

Windows BAT 的參數引用、ASCII/CRLF 格式及共享 PowerShell bootstrap 已檢查；本次仍無 Windows CMD／PowerShell 5.1 或 macOS Finder 真機。Linux 的成功不代表已完成這些平台的雙擊驗收。沒有實際付費 LLM 呼叫。

交付底座與兩個流程各自可獨立打包；整合下載包只是將三個獨立資料夾集合，並未把流程程式納入底座。各 source.zip 保存完整原始碼；不包含環境、模型或私人狀態。

## 0.2.3 一鍵入口（2026-11-15）

run_app.bat／run_app.command／run_app.sh 由來源 project_launcher.py 的 flow_base_cli_v1 擴充生成，沿用既有 bootstrap。source.zip 含原 launcher、validator 及明示擴充、規格、AGENTS 與 todo；沒有為終端工具加入 web server、port 或前端。CLI 擴充的 strict 結構檢查通過，不聲稱符合原來不涵蓋此情境的 web 矩陣。

59 項完整測試通過，包含五項真實 PowerShell 7.6.6 測試、四項 one-click shell wrapper 的成功／失敗／重試／參數傳遞與日誌範圍測試。後續另加入 PowerShell 故意損壞 wheel 的失敗診斷測試；六項 PowerShell、四項 one-click 與封裝檢查共 11 項再次通過，驗證在處理既有 .venv 前寫入 failed 狀態及日誌。

Linux 乾淨解壓測試將 PATH 限制為 shell 與下載／解壓工具，不含 uv、python、python3 或 pip。由 run_app.sh 自動下載並校驗 uv 0.13.6、取得 Python 3.12.16、建立 .venv、安裝底座，最後進入自然語言入口並列出 ai_flow02。安裝階段日誌與 ready 狀態均存在。第二次執行成功，pyvenv.cfg 時間戳未改變，也未再次建立 .venv。

測試用 PowerShell runtime 與下載套件不在交付 ZIP 中。Windows 使用內建 PowerShell，不需要安裝測試所用的 PowerShell 7。Windows CMD 雙擊、Windows PowerShell 5.1 完整安裝、macOS Finder 雙擊與系統安全提示未真機驗收。使用者本機政策、檔案管理器或解壓軟體可能影響雙擊權限，README 已明示。

## 0.2.2 多個 uv 的 PowerShell 修正（2026-11-15）

使用者的 Windows PowerShell 5.1 截圖證實，0.2.1 將多個 ApplicationInfo.Source 路徑傳入呼叫運算子，導致 CommandNotFoundException。0.2.2 改為列舉候選、逐一執行 --version，只回傳一個可用的 System.String 完整路徑；確認 uv 可執行後才進入舊 .venv 的保留／重建階段。

本輪取得 Microsoft 發布的 PowerShell 7.6.6 Linux x64 二進位包，依同版本 hashes.sha256 校驗後，在獨立測試目錄執行。五項可重現測試通過：三個 uv 取第一個、首個失敗取第二個、前兩個失敗取第三個、空候選回傳 null，以及完整打包後 start.ps1 的 PowerShell 語法檢查。三個候選路徑均含空格；同時確認選取階段未搬移既有 .venv。測試使用實際 Get-Command 與原生執行檔呼叫，沒有將 PowerShell 行為改寫成 Python 模擬。

重現：先執行 release，再將 FLOW_BASE_TEST_PWSH 指向當地 pwsh 執行檔，執行 pytest tests/test_powershell_bootstrap.py。沒有 pwsh 時這組測試會明確 skipped。Linux PowerShell 7.6.6 的測試仍不是 Windows PowerShell 5.1 真機驗收；不宣稱所有 Windows 安裝步驟已通過。

## 0.2.1 啟動器修正（2026-11-15）

實際執行 50 項測試全數通過。新增回歸案例：錯誤 Python 在安裝前被阻擋、失敗修復仍保留舊環境、安裝記號不能掩蓋錯誤 Python，以及非虛擬環境的同名資料夾不受影響。

Linux 真實整合測試先建立並執行 Python 3.10.22，再透過打包後的 start.sh 修復：原環境檔案保留，新的 .venv 為 Python 3.12.14，成功安裝 flow_base 0.2.1 並執行 inspect。另驗證遺失 Python 執行檔的環境可重建。這不是 Python 3.10.10 或 Windows 真機測試；PowerShell 同步實作檢查，但本環境未提供 pwsh／Windows，不能宣稱通過 Windows 執行驗證。

0.2.1 只更新底座包；流程 API 維持 0.2，ai_flow01／ai_flow02 不需改版。

驗證日期：2026-11-15。環境：Linux x86_64，Python 3.12；未以其他平台測試結果代替真機證據。

## 實際執行

- 自動測試涵蓋底座單獨啟動、多任務發現、flow_id 自動與併發登錄、版本更新保留 ID、run_id 每次不同、失敗執行記錄、flow_id 作為執行入口、共用狀態與日誌隔離、搬移後沿用 job/cache、輸出衝突、快取損壞恢復、套件竄改、ZIP 路徑穿越、模型缺失、子程序 timeout 與無 shell 求值。
- 自然語言測試涵蓋兩個任務、缺少輸入、多任務衝突、否定、幣別不明、未登錄任務、意圖參數白名單、未設定 LLM、模型路由快取與預算阻擋。
- 字幕測試涵蓋原時間軸、空語音、word timing 分段、改字後保留 segment 時間、非法時間戳、敏感校訂拒絕、LLM 費用保留、快取、未明回應不重送、併發預算保留與 GPU 失敗回退。
- shell 首次啟動：根目錄當時沒有 .venv；啟動後現場建立，僅安裝底座的輕量依賴。
- 冷啟動：PATH 限為 /usr/bin:/bin，沒有現成 uv 與 Python 3.12。成功取得並驗證 uv 0.13.6，下載 Python 3.12.16，建立 .venv，載入兩個任務。
- 搬移啟動：將工作區移至含空格與中文字的另一個路徑；使用 shell 入口成功重建 .venv，沿用已下載 Python 與套件快取。跨作業系統的完整搬移未實測。
- 真實影片：使用約 30 秒的雙語音段落測試 MP4（兩段英文 JFK 樣本，中間有靜音），實際解碼、ONNX VAD、Whisper tiny CPU、SRT/VTT 匯出成功。辨識段落為 2.14–12.58 秒與 18.66–28.42 秒，證實沒有把中間靜音壓縮掉。模型由同工作區先前已驗證的模型匯出檔匯入，再逐檔校驗；本輪未另測 HF 全新下載。
- 相同真實影片工作重跑，四步均可使用快取；flow_id/job_id 沿用，run_id 不同，報告與歷史列可追查。
- 所有交付 ZIP 與 source.zip 均檢查沒有 .venv、已下載模型或大套件；底座 wheel 沒有字幕程式、ASR worker 或 profile。每個任務包含自己的完整原始碼。

## 重現命令

將三包的 source.zip 解至同一個開發目錄後：

```sh
uv sync --project base --group dev --locked
python tools/release.py
uv run --project base --group dev pytest tests ai_flow01/tests -q
sh -n dist/workspace/flow_base/start.sh
sh dist/workspace/flow_base/start.sh list
```

真實語音測試需要自行提供音訊／影片，不包含在發行包內。以 ai_flow01 README 的 run 命令執行，第一次將建立任務依賴環境與模型快取。

## 未驗證範圍

Windows/macOS 真機、實體 CUDA、全新 Windows CUDA 動態庫安裝、其他 Unix、真實付費 LLM、中文課程準確率、長達數小時的壓力測試與斷電恢復都未在本輪完成。沒有將示範字幕匯出誤報為語音辨識，也沒有把 mocked provider 回應視為真實付費呼叫成功。

此版本是可執行與可擴充的初版，跨平台發行前仍需對目標學員的 OS／架構做安裝驗收。
