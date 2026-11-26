# 能力與工具綁定驗證紀錄

## Stage 0 基線與回復點

基準 Git HEAD 為 `fc69b834bc4bc9291ffa48bfdd78f0feadc8cec6`。工作樹另含先前藍圖補強、模型配額與 Windows 啟動修正，測量以當時工作樹為準。發行測具採 `dist/candidate-model-budget-clean-20261126`。測試結果放在 `dist/binding-stage0`。

| 查核項目 | 本次證據 |
| --- | --- |
| 三入口 | 同一藍圖由開發底座 CLI 建立，真實 MCP stdio 取得相同工作狀態。同一連線啟動 Studio 子程序，設計端 Python 在 `workflow_studio/.venv`，傳入的 `--runtime-python` 指向 `flow_base/.venv`。Studio 尚無獨立的綁定狀態回覆。見 `entrypoint-processes.json`。 |
| Runtime 工具清冊 | Python 均為 3.12.11。開發底座 2 個可攜 Adapter、開發 Studio 0 個，前版正式底座 7 個。見 `runtime-baseline.json`。 |
| Windows 暫存 | 同一 uv、Python、wheel。246 字元的 staging 由 uv 回傳 2，Python 解譯器探測快取寫入回報 Windows error 3。61 字元的 staging 回傳 0。見 `uv-path-baseline.json`。 |
| Windows 新解壓底座 | 完整候選在新解壓目錄執行 `--bootstrap-only` 成功，實際新建 .venv 並查得 flow_base 0.3.0。見 `windows-cold-base.log`。 |
| 串行對照 | 針對既有資格執行迴圈的 8 個獨立替身案例，各等待 500 ms，重跑 5 次。中位數 4,003 ms，最大同時執行數為 1。支持後續在同樣工作負載下量測 Stage 2。見 `serial-baseline.json`。 |
| 回復 | 由前版正式環境的既有 SQLite 備份重建到隔離位置。副本雜湊與來源一致，`quick_check` 為 `ok`，共 24 張表。套件、工具腳本和清冊來源見既有 `dist/rk-budget/rollback-verified/verified.json`。見 `restoration-baseline.json`。 |
| 回歸 | Windows 69 項通過，16 項依原測試規則需要 POSIX。實際改在 WSL 執行其中 4 項 POSIX sh 及 12 項 POSIX PowerShell，全部通過。PowerShell 首輪除 1 項缺 uv 外均通過，加入既有 Linux uv 後 12 項完整重跑。見 `core-baseline.xml`、`linux-bootstrap.xml`、`linux-powershell-unprovisioned.xml`、`linux-powershell.xml`。 |

**Stage 0 通過。** Windows 與 Linux 回歸合計 85 項，這個階段沒有 macOS 原生環境證據。已有資料顯示錯指 Runtime 和 uv 暫存 I/O 是兩個可分辨的故障，可用於 Stage 1 的實際驗收。併行尚待 Stage 2 對照相同測具。

### 重跑方式

```powershell
$env:FLOW_BASE_TEST_PWSH = (Get-Command pwsh).Source
& ./flow_base/.venv/Scripts/python.exe -m pytest -q --basetemp dist/binding-stage0/t --junitxml=dist/binding-stage0/core-baseline.xml tests/test_windows_bootstrap_recovery.py tests/test_powershell_bootstrap.py tests/test_bootstrap.py tests/test_blueprint_completion.py tests/test_binding_qualification.py tests/test_continuation.py tests/test_toolchain_lifecycle.py tests/test_model_call_budget.py tests/test_graph_review_external.py tests/test_mcp_stdio.py
```

暫存檢查在工作區的 `dist` 各建一個 61 和 246 字元的隔離 staging，以同一個已校驗的 flow_base wheel 執行 `uv --no-config pip install --offline --no-deps --no-index --no-build --target <staging>/site --python <底座 Python> <wheel>`。各次將 `UV_CACHE_DIR` 指向同一 staging 內的 `cache`，並記錄退出碼和 stderr。兩個安裝目標都位於測具內。

串行對照以八個具名資格候選執行 `complete_bindings`，用測具置換各候選的量測工作，每次等待 500 ms 再回傳資格通過，記錄開始與完成時間，以時間區間計算重疊。Stage 2 用相同候選與延時比較。

POSIX sh 執行 `python -m pytest -q tests/test_bootstrap.py`。POSIX PowerShell 使用 Linux 原生 PowerShell 7.6.9 與可用的 `uv`，設好 `FLOW_BASE_TEST_PWSH` 和 `ROUTINEKEEL_TEST_RELEASE_DIR` 後執行 `python -m pytest -q tests/test_powershell_bootstrap.py`。本次在 WSL 以原廠 SHA-256 核對 PowerShell 後解壓到臨時目錄，測完清理該目錄。 [PowerShell 7.6.9 發行與校驗](https://github.com/PowerShell/PowerShell/releases/tag/v7.6.9)

## Stage 1 目標 Runtime 與初裝恢復

**B01、B02 通過。** 綁定查核提供實際 Python、前綴、版本、工作區及 Runtime 識別。三入口的同一份測試藍圖通過環境與缺口對照。Runtime 錯配時回報預期與實際 Python，套件探測留待正確 Runtime 續做。

Windows 與 WSL 均從新解壓發行包實跑整合與獨立 Studio。整合版使用工作區根層的 Runtime，獨立版使用隨附的 Runtime，兩者各自保留 Studio 的設計環境。兩平台核對了相同底座 wheel。發行候選位於 `dist/candidate-binding-stage1-combined/release`。

原生 Windows 以相同 uv 與 CPython 重現深路徑 error 3，改用短位置完成 wheel 驗證及離線安裝，同一準備操作再次呼叫回原收據。wheel 與 uv 快取可使用不同的暫存目錄，避開舊版 uv 讀取含 # 的本機 wheel 路徑時的問題。注入兩次失敗時，保存兩組診斷並保留既有環境。

| 驗證 | 證據 |
| --- | --- |
| Windows 核心回歸 44 項 | `dist/binding-stage1/windows-authoritative.xml` |
| Windows 三入口、短暫存與 Studio 設計 14 項 | `dist/binding-stage1/b01-b02-windows.xml` |
| Windows 發行佈局 15 項 | `dist/binding-stage1/windows-release.xml` |
| WSL 23 項，2 項 Windows 專用測例已在 Windows 驗證 | `dist/binding-stage1/linux-stage1-authoritative.xml` |
| 原生 Windows uv 失敗及同操作恢復 | `dist/binding-stage1/windows-real-uv-recovery.json` |
| 原生新解壓與 Runtime 身分 | `dist/binding-stage1/windows-cold-stage1.json`、`dist/binding-stage1/linux-cold-stage1.json` |

Linux 首輪測試的子程序讀到舊契約 wheel；於隔離副本安裝本次 wheel 後重跑通過。本次尚無 macOS 原生環境。退回可沿用 Stage 0 前一版套件與啟動方式。Stage 1 尚未升級資料庫格式。

## Stage 2 持久認領與併行

SQLite v8 保存綁定子工作、依賴、資源鍵、領取世代及結果。工作內容透過操作與作用域識別去重。已過期的本機安全操作可重做，不可安全重送的操作進待查狀態。慢操作在修訂鎖外進行。取消後停止新的認領，過期或改版的結果不能覆寫工作修訂。準備與既有就緒資格能跨分支重疊，合流後依已準備的工具再推進資格。

同一個八項資格測具、各等待 500 ms、單槽與四槽各五次。新排程單槽中位數 **7.815 秒**，四槽 **2.333 秒**，縮短 **70.1%**。實測峰值為四個。此處比較的是含持久領取與寫入的替身工作，正式工具的冷／熱效能另列於 Stage 7。見 `dist/binding-stage2/serial-parallel-benchmark.json`。

WSL 的定向認領與遷移回歸有 19 項通過，包含跨分支等待、雙 Worker、取消、中斷後以同一請求繼續及驗證 v7 備份。見 `dist/binding-stage2/linux-final.xml`。要回到 Stage 1，停止派送與完成回執核對後，使用遷移前的 `state.sqlite3.before-v008.sqlite3` 備份及相符的套件。先核對 SHA-256，再於隔離副本驗證還原。

**Stage 2 通過 B03、B04、B07。** 最後一輪 Windows 有 70 項通過，涵蓋並行檢查、等待資料時推進其餘檢查、暫停／取消、遷移與回復、模型預算、安裝與重綁。CLI 至 MCP 重連另 1 項通過。另以兩個真實 Python 子程序同時認領，只有一個取得操作。見 `windows-final.xml`、`mcp-reconnect.xml` 與 `process-claim-race.json`。

```powershell
& ./flow_base/.venv/Scripts/python.exe -m pytest -q --basetemp dist/binding-stage2/end --junitxml=dist/binding-stage2/windows-final.xml tests/test_binding_operations.py tests/test_db_migrations.py tests/test_blueprint_completion.py tests/test_binding_qualification.py tests/test_toolchain_preparation.py tests/test_toolchain_lifecycle.py tests/test_model_call_budget.py tests/test_continuation.py
```

## Stage 3 本機工具綁定

| 路徑 | 本次驗證 |
| --- | --- |
| 通用 CLI | ffprobe 以校驗的本機執行檔、參數陣列與具名 JSON 輸出完成補強、資格、隔離安裝及 Runtime 實跑。覆蓋含中文及特殊字元的素材路徑、錯誤校驗及拒用外部 URL。見 `dist/binding-stage3/cli-e2e.xml`。 |
| Skill 腳本 | 完整 Skill ZIP 從受信位置準備，驗證腳本成員與本機 Python。經資格、安裝及實際執行交付，修改已保存 ZIP 時會拒絕使用。見 `dist/binding-stage3/skill-e2e.xml`。 |
| faster-whisper Python Adapter | Graph 片段映射已接上既有 Worker，保留來源時間、原文和逐片段語言。替身呼叫及缺模型時停住均通過；新 wheel 在乾淨環境可辨識 entry point 與鎖定模型版次。見 `worker-map.xml`、`packaged-asr-entry.json`。 |
| 本機回歸及 Profile | 17 項本機工具和字幕回歸通過。服務可沿用符合鎖定版本的 CPython，3 項 Profile Python 測試通過。見 `local-bindings.xml`、`profile-python.xml`。 |

首次實機驗收曾缺少 large-v3 的 3,090,835,702 bytes 鎖定權重及 `av==16.0.1`。Hugging Face 快取只有版次指標，OpenAI Whisper 快取有 `base.pt`。取得使用者同意後，已在隔離工作區下載並驗證五個 large-v3 檔案，裝妥 CPU Profile，再由同一個本機套件快取離線建置 CUDA Profile。見 `dist/binding-stage3/asr-prerequisites.json` 與 `native-asr-cuda-acceptance.json`。

純規則 Skill 已補做實測。測試把完整 ZIP 和已確認的附件指紋匯入藍圖，經 Builder 轉成宿主派送的具名規則。派送綁定規則指紋。本機 Gemma 4 E2B 只有一次生成請求，使用結構化輸出，串流結果以相同範圍的回執回填。兩項案例與現有宿主回執及模型額度回歸均通過。見 `dist/binding-stage3/skill-rule-compiled.xml`、`skill-rule-schema.xml`、`model-skill-regression.xml`。

`tools/verify_course_graph_asr.py` 以隔離工作區的已安裝 wheel 指定 Adapter、核對鎖定模型與 CPU Profile、驗證已知合成音檔。實跑時檢查獨立文字預期、兩段原片時間映射、字級時間與具名校稿輸出。已用實際安裝的 wheel 執行前置檢查，確認 Adapter 可用、模型與 Profile 未就緒，退出碼 3（`NOT_RUN`）。見 `dist/binding-stage3/asr-real-preflight.json`。權重及 Profile 備妥後，可用同一份測具和入口重跑。

另以打包後的底座與字幕 wheel，在隔離工作區透過標準藍圖補強入口觸發本機資格驗證。缺少模型時回報 `OFFLINE_RESOURCE_MISSING`，資格為 `NOT_RUN` 且未安裝候選。對工作 `completion_4009ca2d88cee50304d292e3c91b06f6` 實際重驗，修訂從 2 到 4，工作 ID 相同。缺 Python profile 亦有獨立回歸，另確認已有輸出不符時保留 `FAIL`。見 `dist/binding-stage3/native-asr-prerequisite-work.json`、`native-asr-prerequisite-recheck.json` 與 `qualification-prereqs-latest.xml`（10 項通過）。

本機 Adapter 資格案例可依每個具名輸出提供 `expected_schema`，檢查語音辨識等會產生合理變化的輸出。逐值預期仍可用於固定輸出。僅回傳雜湊的宿主回執需要逐值預期。新版底座與字幕 wheel 以兩組字幕案例實跑補強入口，工作 `completion_cf43e8f3e7abc8f80e20e997f8a9995e` 回報 `OFFLINE_RESOURCE_MISSING`、`NOT_RUN`，且候選未安裝。14 項回歸通過，覆蓋符合與不符合 schema、禁止外部 schema 參照、宿主回執證據不足。見 `dist/binding-stage3/native-asr-schema-work.json` 與 `qualified-output-contract-tests.xml`。

要產生字幕資格案例，可在下列命令加上 `--write-qualification-cases <新檔路徑>`。兩組案例檢查已知句子、語言、不同的時間窗、實際辨識裝置及校稿欄位。原工作的逐值案例已納入指紋。更新該工作的案例須經藍圖修訂與確認，亦可在隔離測試工作中使用新案例。

以新版底座及字幕 wheel 實際執行離線 `uv sync`，缺 `av==16.0.1` 時會留下 Python 執行檔。字幕 Adapter 現在也檢查成功安裝的環境位置紀錄。以替身提供模型位置來隔離驗證此條件，Adapter 回報 `ENV_MISSING`，未派送 Worker。資格、字幕映射與 Profile 共 21 項回歸通過。見 `dist/binding-stage3/native-incomplete-profile-probe.json`、`asr-incomplete-profile-preflight.json` 與 `incomplete-profile-tests.xml`。

字幕 Graph Adapter 使用底座硬體偵測和字幕的既有裝置選擇。`auto` 偵測到 CUDA 時先回報 `COMPUTE_DEVICE_CONFIRMATION_REQUIRED`。確認後將 `device` 設為 `cuda` 或 `cpu`，並納入資格案例。實測原補強工作先停在裝置確認，資格為 `NOT_RUN`。使用者同意後，以已安裝的新版 wheel 在 RTX 5090 Laptop GPU 實跑兩段合成語音。實際裝置、辨識句子、原片時間、字級時間及校稿欄位均通過。見 `dist/binding-stage3/native-asr-device-gate.json` 與 `native-asr-cuda-acceptance.json`。

Graph 實跑會將已完成語音區段的檢查點存入該工作的本機狀態。Worker 在單次請求中逐區辨識，共用一次模型載入，續跑重用檔案與設定指紋相符的區段。以最新 wheel 在 RTX 5090 實測，先完成第一區，再從同一檢查點續接兩區，回報第一區 `cached`、第二區 `executed`，第一區結果相同。另修正 Windows 資格子程序遺失 CPU 架構資訊造成 Profile 路徑不同的缺陷。正式資格入口在隔離測試藍圖的兩個 CUDA 案例均重新推論，核發資格收據及綁定鎖。58 項回歸全數通過。見 `dist/binding-stage3/native-asr-cuda-resume-probe.json`、`native-asr-cuda-official-qualification.json` 與 `gpu-resume-tests.xml`。

原補強工作保留在裝置確認缺口，Studio 修訂初稿已建立。隔離測試藍圖的資格收據綁定該測試稿，原工作須沿 Studio 校閱後的修訂更新。正式長片與正式工作區的版本更新另行驗收。

```powershell
$workspace = 'dist/binding-stage3/native-schema-check'
$speech = 'dist/course-subtitle-adapter/workspace/share_states/blueprint_files/2869b5a810bcfe3004276861680b0d764b5573fdf3804b3072b517af72d7f2dc.zip'
# 取得使用者確認後，帶入已確認的 cuda 或 cpu。
& "$workspace/.venv/Scripts/python.exe" -I -B tools/verify_course_graph_asr.py --workspace $workspace --speech-zip $speech --device cuda
```

## Stage 4 宿主派送

先重跑宿主派送、授權範圍、外部回執、模型用量及 CLI 到 MCP 重連，22 項通過。見 `dist/binding-stage4/host-baseline-tests.xml`。

`host-dispatch` 已用真實本機 MCP stdio 測試伺服器驗證。從已驗證並安裝的合成工作流取得待辦，確認 CLI 派送、宿主補上的輸出 Schema 與具名映射、工具回報錯誤、連線中斷、伺服器要求補輸入、已有回執卻未回填、同時領取。七個案例的伺服器日誌各記錄一次 `tools/call`。有回執時可沿原待辦回填，結果不明時維持 `RECONCILE_REQUIRED`。含既有清冊與對外操作回歸共 27 項通過。見 `dist/binding-stage4/stdio-single-dispatch-tests.xml`。

宿主模型與 MCP 工具共用 `host-dispatch`，模型派送固定 loopback 端點、已安裝模型指紋、裝置、輸出上限與具名輸出契約。GPU 選擇須先取得本次待辦的核准。使用已配置的本機 `gemma4:e2b` 在 CPU 執行合成文字案例，CLI 逐段送出 delta，底座驗證完整串流並交付具名 JSON。回執為 1 次模型呼叫、59 個輸入 token、9 個輸出 token、8 則增量。回歸涵蓋未知結果待查、原回執恢復、輸出違反 schema、模型版本漂移、GPU 核准與可攜綁定指紋。8 項模型驗收與 42 項派送、重連、租約及取消回歸全部通過。見 `dist/binding-stage4/host-model-acceptance.xml` 與 `host-b07-acceptance.xml`。

停止新的 `host-dispatch` 即可停止新派送。既有工作仍可用 `blueprint-task` 查詢及對帳；結果未知的操作保持原領取狀態。兩種執行器均沿用既有宿主回執及工作資料格式。

## Stage 5 工具提議與串流解析

本機 `gemma4:e2b` 在 CPU 合成案例提出 `uppercase`，回傳工具呼叫 ID、索引及物件參數。此探測記錄提議，共執行零次工具。見 `dist/binding-stage5/ollama-tool-stream-probe.json`。

Studio 的 Chat Completions 傳輸新增明確的工具模式。解析器按索引組合分段參數，確認完成標記後才發布完整提議。案例驗證同名不同 ID、同 ID 重複、衝突參數、截斷及單輪最多四項。純文字入口沿用既有串流與 JSON 模式。最終解析器 15 項測試通過，含原串流執行入口的合併回歸 20 項通過。見 `dist/binding-stage5/tool-fragment-final.xml`、`tool-fragment-stream-tests.xml`。 [Chat Completions 工具串流](https://developers.openai.com/api/docs/guides/function-calling#streaming)

底座已接通 Ollama 的本機工具循環。可用工具引用工作流中已綁定的純本機節點，工具清單與參數合約固定在派送範圍。模型請求與工具執行均先寫入扣抵紀錄。獨立工具並行派送，依原 ID 順序回填。隔離安裝實跑 `gemma4:e2b`，由模型提出 `uppercase`，底座執行一次並回填，模型於第二次請求交付 `EVIDENCE-READY`。見 `dist/binding-stage5/native-tool-dialogue.xml` 與 `tool-round-journal-tests.xml`。 [Ollama 工具往返](https://docs.ollama.com/capabilities/tool-calling)

底座的 28 項本機驗收及回歸通過，包含實機工具往返、核准、並行、超額截斷及字幕的呼叫上限回歸。另有一項工具完成後的模型回填斷線測試，已完成的具名結果留在原操作紀錄，重開宿主時不重做工具或模型請求。Studio 純文字與解析回歸共 20 項通過。見 `dist/binding-stage5/tool-local-acceptance.xml`、`tool-round-recovery.xml` 與 `studio-stream-continuation.xml`。

已綁定的本機 MCP stdio 工具沿用相同派送與核准範圍。實際 Gemma 呼叫測試伺服器並取得子操作回執，兩次模型請求完成輸出。MCP 斷線時，帳本保留子操作 ID 與未知結果，原待辦維持 `RECONCILE_REQUIRED`。最後 3 項 MCP 驗收全數通過。見 `dist/binding-stage5/mcp-final-qualification.xml`。

以替身模型提出呼叫，實際執行隔離安裝的可攜工具。Python 腳本回傳 `count=8`，ffprobe 在模型指定的第二個素材回傳 `sample_rate=16000`，前置節點使用的第一個素材是 `8000`。兩案例均核對原工具 ID、資格收據及獨立輸出。14 項相依工具與字幕用量回歸通過。Stage 5 通過 B05 與 B08 對應驗收。見 `dist/binding-stage5/portable-tool-final.xml`、`tool-dependency-b08.xml`。正式環境的字幕長片仍待驗收。

## Stage 6 三條補件路徑

已通過五項基線，涵蓋本機替代方案排序、鎖定 wheel 的直接準備及工具鏈準備、受限 WASM 的驗證安裝與重驗，以及獨立的準備和資格驗證並行。見 `dist/binding-stage6/remediation-baseline.xml`。

同一缺口有多份相容配方時，分別展示已獲宿主授權的版本和配方指紋。以兩份獨立 fixture wheel 實跑，選定第二份後，同一個 `work_id` 只準備、驗證並安裝第二份。另以未授權模式起案，先保存 uv 原廠的版本及雜湊說明，確認零安裝。更新授權後，沿同一工作重查並選定，完成資格與安裝。5 項選擇及準備回歸與 1 項研究、授權續做案例通過。見 `dist/binding-stage6/scoped-preparation.xml`、`research-approval-resume.xml`。 [uv 官方雜湊驗證說明](https://docs.astral.sh/uv/reference/cli/#uv-pip-install--require-hashes)

兩個缺口的實跑確認，未獲準備授權的分支仍等待，另一分支先安裝鎖定 wheel 並取得資格。重開服務、更新第二分支授權後，同一 `work_id` 沿用第一個資格收據，完成第二分支及隔離安裝。另測試排入佇列後撤回授權，執行邊界攔住準備，恢復授權後沿原操作續做。見 `dist/binding-stage6/approval-boundary-cases.xml`。

沿用已核准的 PyAV 16.0.1 Windows CPython 3.12 wheel 驗證原廠來源。先確認缺下載授權時停在 `PREPARATION_DOWNLOAD_APPROVAL_REQUIRED`，再從 PyPI 取得 32,296,912 bytes，核對 SHA-256 並離線安裝。資格通過後，Windows 試做在過長暫存路徑載入 PyAV DLL 時失敗，診斷已保存失敗節點與程序原因。改用較短的工作區暫存位置後，同一工作用原快取通過隔離試做並安裝。停用下載與準備授權後成功離線執行，回傳 PyAV 16.0.1。見 `dist/binding-stage6/live-av-source/verified-source.json`。 [PyAV 官方版本資料](https://pypi.org/pypi/av/16.0.1/json)

來源驗證拒絕非 HTTPS、非 PyPI 檔案主機、檔名或版本錯配，以及含 query 的網址。失敗回歸確認 SHA-256 不符時不寫入安裝收據，正確檔案到位後沿原工作重驗。工具回執綁錯作用域時，原操作維持 `RECONCILE_REQUIRED`。Windows 的 uv 靜默退出會保留初次診斷並短路徑重試。隔離試做程序退出且沒有結果時，記錄退出代碼，僅在全新隔離環境重試一次。見 `dist/binding-stage6/source-uri-guards.xml`、`receipt-scope.xml` 與 `b04-b06-rerun.xml`。

**Stage 6 通過 B04 與 B06。** 最終 Windows 驗收 20 項通過。包含分支等待授權及重啟復用、派送前撤權、原廠 wheel 與雜湊、配方選擇、WASM 自行建置、ffprobe 特殊字元、回執錯配與隔離程序恢復。見 `dist/binding-stage6/b04-b06-rerun.xml`。

## Stage 7 整合驗收

Windows 的 B03 替身重測使用八項獨立操作，各等待 500 ms。單槽和四槽各跑五次冷執行和五次暖執行，另查核同一工作的結果復用。冷執行使用新工作區，暖執行沿用資料庫並發出新子工作。四槽同時執行峰值為四，所有輸出指紋一致。冷執行的中位耗時縮短 67.45%，通過至少 25% 的目標。已通過的操作重讀時共執行零個子工作。見 `dist/binding-stage7/windows-synthetic-benchmark/benchmark.json`；重跑腳本為 `dist/binding-stage7/benchmark-bindings.py`。

| 情境 | 單槽 p50／p95 | 四槽 p50／p95 |
| --- | --- | --- |
| 冷執行 | 6.672／6.813 秒 | 2.172／2.375 秒 |
| 暖執行 | 6.281／6.375 秒 | 1.859／1.937 秒 |

Windows 已用最新整合及獨立發行包分別新解壓，重用先前的本機套件快取離線建立虛擬環境。整合版選用工作區根層 `.venv`，獨立版選用隨包工作區 `.venv`。兩者的流程 Runtime wheel SHA-256 相同，為 `6a0eabd7bd60dafddce537a620194ce5044b43bb10686775c8c15fdf78ef55d4`。見 `dist/binding-stage7/windows-cold-stage7.json` 及同目錄的啟動日誌。

Windows 也用已驗證的 ffprobe 管線實跑八個本機 WAV 測試素材，各槽數冷、暖執行各五次。冷執行含準備的 p50／p95，單槽為 6.115／6.382 秒，四槽為 2.282／2.418 秒。暖執行單槽為 5.431／5.621 秒，四槽為 1.702／1.817 秒。四槽峰值為四項，兩種排程都交付預期的八種取樣率，輸出指紋一致。排隊、單筆呼叫與結果復用量測見 `dist/binding-stage7/windows-ffprobe-benchmark/benchmark.json`；重跑腳本為 `dist/binding-stage7/benchmark-ffprobe.py`。

本項耗時來自真實 ffprobe 執行與合成 WAV 素材。正式字幕長片仍待實跑。

WSL 原生 Linux 使用同一份最新候選包，分別新解壓整合版與獨立版，重用既有依賴快取離線建立執行環境。兩者的 Runtime wheel SHA-256 均與 Windows 一致。Linux 的啟動、打包佈局、認領、SQLite 遷移回復及重綁續做共 46 項通過，2 項專用 Windows uv 暫存情境依平台分流。見 `dist/binding-stage7/linux-cold-stage7.json`、`linux-release-restore.xml` 與重跑腳本 `linux-release-restore.py`。

另外從真實 ffprobe 量測工作區備份 SQLite，以唯讀方式核對 SHA-256 後還原至新目錄。還原後完整性為 `ok`，16 筆操作的指紋一致，重開執行器沿用 8 筆既有回執，未再次呼叫工具。見 `dist/binding-stage7/sqlite-restore/restore.json`。

對已驗收候選包的副本注入兩種變更。IR 異動會被拒絕。異動 wheel 內容後重算外層 SHA-256，ZIP CRC 仍有效，驗證器會依不符的內部 RECORD 拒絕候選。原候選及原始 IR 均通過。見 `dist/binding-stage7/record-admission/record-admission.json`。

Windows 的綁定認領、呼叫上限、外部回執、MCP 重連與等待授權後續作共 29 項通過。安裝與回復整合批次為 53 項通過、4 項待排解。兩項三工具流程等候安裝超時，另外兩項為 Studio 預檢回傳欄位及匯出重驗。見 `dist/binding-stage7/windows-binding-budget.xml`、`windows-release-restore.xml`。

兩項三工具流程另在短路徑重跑。一般安裝及執行通過，有人工檢查點的案例仍在第二個試做案例的核對階段耗盡測試等候時間。見 `dist/binding-stage7/windows-toolchain-short.xml`。

後續逐階段量測確認，含人工檢查點的三工具流程完成首次安裝約 54.4 秒，工具漂移後的第二次候選試做約 62.6 秒，同一工作重綁與原成果沿用通過。兩組試做均超過舊測具 45 秒等待上限。候選現在只準備一次共用環境，每案仍個別計畫、重核綁定、執行並比對輸出。針對三工具及多次重綁案例，測具等候上限調為 120 秒。見 `dist/binding-stage7/windows-toolchain-timing.xml`。

Studio 匯出現沿用宿主交給它的底座 Python。沒有明確傳入時，用工作區的 Runtime 環境定位規則。新版 Windows 和 WSL 都完成整合與獨立包的離線冷啟動，Runtime wheel 的 SHA-256 均為 `b8c60872a90b7d052cf6f71d50db91b87a5beb5ac19183ab5601d2760c2dc1f6`。WSL 55 項通過，含 Studio 匯出、三工具重綁及資料庫回復，2 項專用 Windows 測試分流。見 `dist/binding-stage7/retest/windows-cold-stage7.json`、`linux-cold-stage7.json`、`linux-release-restore.xml`。

Windows 以前版實裝 wheel 及資料庫 v7 執行檔案複製，備份後安裝候選，資料庫升至 v8 並保有原 Run。在新目錄以原版 wheel 和原備份回復至 v7，核對原 Run 後再次成功產生結果。驗證過的套件、備份指紋及 Run 見 `dist/binding-stage7/retest/local-restore/report.json`。

以本機 Gemma 4 E2B 的 CPU 模式重跑純文字串流及 MCP ToolCalling 往返，兩項通過。見 `dist/binding-stage7/windows-real-model.xml`。

Windows 另跑 16 項工具實作測試，皆通過。涵蓋 Skill 腳本、規則型 Skill 實際模型回覆、ffprobe 安裝後執行、真實 MCP 伺服器、程式及 CLI 工具往返與回執恢復。見 `dist/binding-stage7/windows-tool-types.xml`。獨立 Studio 正式封包的兩種匯出案例均通過。見 `dist/binding-stage7/retest/windows-standalone-export.xml`。

補件、併行及字幕額度批次的 27 項中，25 項首次通過。等待另一支授權的續作與 WASM 安裝各遇 45 秒測具限時。保留原驗收斷言後使用 120 秒測具上限重跑，兩項均通過。等待授權案例核對原工作與另一支已通過的收據，WASM 案例以實際執行器驗證建置與安裝。見 `dist/binding-stage7/retest/windows-b04-b06-b08.xml`、`windows-approval-resume.xml`、`windows-wasm-remedy.xml`。

### 課程字幕逐段辨識

已安裝舊版在模型程序中逐段辨識，要等全部片段完成才回傳。字幕候選 0.5.2 以單一 faster-whisper 程序直接從 ZIP 逐段讀取，保存各段音訊、設定及成果的指紋，並將已完成數及下一段寫入同一 Graph 檢查點。完整結果才傳給校稿。正式工作區換綁與長片驗收仍待執行。

模擬第二段失敗時保留第一段，第三段尚未讀取。重試只辨識第二、三段。再次重試時全數命中檢查點且不載入模型。新版亦驗證可讀取 0.5.1 已完成的辨識紀錄並只恢復後段產物。故障與 Adapter 對接共 11 項通過，辨識及舊版回復專項共 42 項通過。見 `dist/asr-loop/fault-final.xml`、`recovery.xml`。

從 `flow_subtitles-0.5.2.zip` 取出 wheel，在隔離工作區離線安裝並重建 CPU Profile。使用先前校驗的 large-v3 權重及聲音測具，核對已知句子、實際裝置、兩段原片時間、詞級時間及具名校稿輸出。在同一檢查點先完成一段，再擴充到兩段，第一段結果完全相同，進度記錄一段沿用、一段推論。見 `dist/asr-loop/native-qualification.json`、`native-resume.json`。

以最新整合候選的底座及字幕 0.5.2 wheel，在隔離工作區由 `blueprint-completion` 資格驗證兩組 CPU 語音測例，安裝前通過 Studio 重驗及隔離載入。再以已安裝流程執行合成語音，從工作流事件觀察 0／2、1／2、2／2，核對詞級時間和具名輸出。另用該候選的副本驗證 IR 異動以及 wheel RECORD 失配都會被攔截。見 `dist/binding-stage7/retest/asr-cpu-installed.json`、`asr-cpu-completion.json`、`record-admission/record-admission.json`。

**Stage 7 本機整合驗收通過。** Windows 安裝與回復 57 項、補件及額度 27 項皆有逐案通過證據。重新執行中斷點、授權續做及 WASM 案例後，逐一核對原批次的同名案例與結果。Linux 原生範圍為 WSL，通過 55 項，另外 2 項 Windows 暫存專項已在 Windows 通過。macOS、本次新版 CUDA 與正式課程長片未執行。見 `dist/binding-stage7/retest/stage7-matrix.json`。

## Stage 8 文件與本機交付

已將操作與回復指南收進本機發行包。`dist/candidate-binding-stage8-local/release/versions.json` 及 `SHA256SUMS` 記錄五個 ZIP。整合包 `RoutineKeel-workspace-0.3.0.zip` 的 SHA-256 是 `becb755e67619848de2542e6fbed49db24fe6862d1afd1c52de5cbbaad6bb207`。底座、共用契約、Studio 與流程 wheel 逐一比對，均與 Stage 7 經驗收的 wheel 位元組一致。前版原始發行包保留在 `dist/candidate-binding-stage1-combined/release/`，已驗證的 v7 備份與回復紀錄保存在 `dist/binding-stage7/retest/local-restore/`。

最終 ZIP 從乾淨路徑解壓。Windows、WSL 均完成整合與獨立 Studio 離線啟動。再用兩邊的正式底座 CLI 從空白 catalog 匯入測試藍圖，完成隔離試做、安裝、`plan`、`verify` 和實際 `run`，都交付具名結果 `{"output":"TEST"}`。`doctor` 也核對了 Python、uv、寫入權限及 SQLite v8；本機資源由選定流程的 `verify` 查核。兩平台報告在 `dist/binding-stage8/` 的 `windows-local-delivery.json` 和 `linux-local-delivery.json`。

可從儲存庫根目錄用已備妥的本機 Python 重播。腳本依平台使用已核對的離線套件快取，在新位置建立執行環境及測試工作區。Windows 的指令如下。WSL 以原生 `python3` 執行相同腳本，將 ZIP 和輸出目錄改為 WSL 可存取的路徑。

```powershell
python -u -B dist/binding-stage7/verify-local-delivery.py dist/candidate-binding-stage8-local/release dist/binding-stage8-replay
```

**Stage 8 本機交付通過。** 受測範圍為 Windows 與 WSL。本機候選可供隔離安裝和回復驗證。正式工作區的啟用、真實課程長片九項驗收、macOS 原生與新版 CUDA 仍待各自的實測或授權。
