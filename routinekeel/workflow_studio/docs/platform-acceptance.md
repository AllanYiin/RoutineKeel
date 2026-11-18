# 平台與安全端到端驗收

## 本機新增證據

- `test_security_end_to_end.py`：未授權 email.send 描述與來源自授權文字不能進入可執行／核准／發布鏈，
  SMTP／socket／HTTP 探針呼叫為 0，舊發布包完整且仍可執行。沒有為測試新增真正的郵件適配器。
- 兩個獨立驗證及核准的專案即使使用同一 workflow_id，也不能覆寫另一專案的目標；一般發布與歷史回復均拒絕，外來 approval ID 也拒絕。
- `test_acceptance_lifecycle.py`：第三個未發布 IR 修訂（ir_ready）重開後，來源版本、內容指紋及人工確認紀錄還原；
  續編第四版再試做通過，原發布目錄、第一版及第三版都不被修改。
- 模擬真實串流先有片段再斷線，保存 analysis.failed／LLM_STREAM_INTERRUPTED，呈現未完成訊息，
  重開仍是上一有效候選；沒有把不完整內容存為可執行版本。畫面訊息僅控制器驗證，未操作原生視窗。
- JSON 比較已使用 Decimal 與逐值型別檢查；boolean／number、string／number、缺欄位、精度、重複 key、NaN 等既有測試通過，未無故重寫已正確的比較器。

上述安全／生命週期／比較三檔：**30 passed，22.10 秒**。第一個寄送測試曾先被 external 節點的 cache 禁令擋下，
測試修正為合法的 no-cache／不重試結構，再確認目標 TOOL_NOT_ALLOWED；沒有放寬產品規則。

## 最新底座基線

2026-11-18 從目前工作樹、底座獨立環境執行：

```powershell
& routinekeel/flow_base/.venv/Scripts/python.exe -m pytest tests routinekeel/flows --ignore=tests/studio -q -rs -k 'not test_tk_window_renders_real_preview_and_does_not_connect_network' --junitxml=dist/studio-gates/2026-11-18-security/runtime-regression.xml
```

結果：**383 passed、21 skipped、1 deselected，287.62 秒，沒有失敗**。
21 個 skip 為 POSIX sh／PowerShell-on-POSIX 條件；1 個原生 Tk 測試依停止桌面操作限制排除。
使用者既有的底座與測試修改仍保留，不能把這次結果當成乾淨發行 tag 的證據，也不把別人的修正算成本輪修改。
Studio 另用自己的依賴環境驗證，這不是從整體驗收移除 Studio。

## 三平台 CI 設計

`studio-acceptance.yml` 是可重用及可手動觸發的 workflow；packages 與 platform-checks 都呼叫它。
標準 runner 使用 ubuntu-24.04、windows-2025、macos-15，對應 [GitHub 官方 runner 清單](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)。

- Runtime 與 Studio 各自 `uv sync --locked`，不讓底座測試環境誤收缺少依賴的 Studio 測試。
- 以獨立 Runtime Python 確認無法 import workflow_studio；生成流程包仍須獨立執行。
- Studio 非桌面完整套件、Ruff、五項基準、故障矩陣及 JUnit 都在三種 OS 上執行；缺少 FFmpeg 的 P2 測試會明列 skip。
- 保存環境、實際 commit、基準 JSON 與 JUnit；不附整個工作區、憑證或私人案例。
- packages 的正式發布 needs 同時包含底座與 Studio；原本 main＋workflow_dispatch＋publish_release=true 的手動發布 Gate 保留。

`test_platform_matrix.py` 使用固定來源 ID，讓三平台的 IR SHA-256 可對照。
每個 job 只把實際所在 OS 記為已執行，其他 ToolPolicy 是靜態生成檢查；不能用本機輪流填三個平台名稱冒充三平台實測。
本機 Windows 此項 1 passed，2.17 秒；CI 的實際狀態須另以 run／job 結果記錄，設定檔存在不等於通過。

CLI 的 gh 尚未登入；Git 既有 credential manager 在無互動模式可讀 remote。GitHub 連接器可查 repo 與 PR／Actions 結果，
新的 workflow 透過草稿 PR 觸發。尚未取得三平台成功結果前，AC-07 保持待驗。

## 首次 CI 與修正

草稿 [PR #9](https://github.com/AllanYiin/RoutineKeel/pull/9) 的整合版本 `1663f4f`
在 [run 44236192522](https://github.com/AllanYiin/RoutineKeel/actions/runs/44236192522) 完整執行：

| 工作 | 結果 |
|---|---|
| 底座 Ubuntu／Windows／macOS | 三者 success |
| Studio Windows | 518 passed、1 failed、6 skipped、3 deselected；894.84 秒 |
| Studio macOS | 398 passed、121 failed、6 skipped、3 deselected；21.41 秒 |
| Studio Ubuntu | 398 passed、121 failed、6 skipped、3 deselected；35.81 秒 |
| 正式發布 | skipped |

POSIX 的共通問題是 Verifier 對 Runtime Python 使用 `Path.resolve()`，把虛擬環境的
`bin/python` 符號連結解析成系統 Python，因而遺失獨立環境的依賴。WSL Python 3.12.3
用原始建構子重現：選定 venv 卻啟動 `/usr/bin/python3.12`、`sys.prefix=/usr`；改用
`absolute()` 後實際子程序留在所選 venv。新增明確路徑、相對路徑、目前 Python 三種選擇的
真實子程序測試，CI 將使用原生 POSIX venv symlink 驗證。

Windows 的唯一失敗來自舊模板測試：Git 已將 `.txt` 取出為 CRLF，測試再把每個 LF 換成
CRLF 而產生 CRCRLF。現在先正規化測試資料，再分別用 LF／CRLF 建立舊 wheel，兩者均須
通過 Verifier 與實際 Runtime 執行。未放寬產品的可信模板比對。

修正後本機相關五檔 **16 passed，40.54 秒**；舊模板 **3 passed，6.14 秒**；
Ruff check／format **172 檔通過**。證據在 `dist/posix-repro/`，修正版本仍須重新取得三平台 CI 結果。
6 個 skip 是缺少 FFmpeg 的 P2 影片測試，3 個 deselected 為未授權重啟的原生 GUI 測試；兩者不能宣稱通過。

## 修正版本 CI（3924c6d）

[run 44245189603](https://github.com/AllanYiin/RoutineKeel/actions/runs/44245189603)
對應 PR head `3924c6dc2b271a81389dbee3bec9f248544a3966`；GitHub 實際 checkout 的 PR merge commit
為 `2e27d6105e59277859986bbd9f84729619a200e8`，由各 artifact 的 environment.json 核對。
首輪 head `1663f4f` 的實際 merge commit 則是 `4e348218325251183062be7570ae58057cea032a`。

| 範圍 | Ubuntu 24.04 | Windows 2025 | macOS 15 |
|---|---|---|---|
| 底座與流程（含 optional MCP） | 409 passed／3 skipped | 386 passed／26 skipped | 410 passed／2 skipped |
| Studio 非桌面套件 | 523 passed／6 skipped／3 deselected | 523 passed／6 skipped／3 deselected | 523 passed／6 skipped／3 deselected |
| Studio 耗時 | 214.65 秒 | 1278.82 秒 | 128.09 秒 |

此 run 已 completed/success，六個測試 job 均 success，正式 publish skipped。
三平台 artifacts 均含 20 個固定發布故障報告、三個真正程序中止及 100 次隔離執行紀錄；
100 次實際成功、不同 run ID、成果重核通過、registered model calls=0。
20 節點靜態驗證 p95：Ubuntu 1.763391 ms、Windows 1.5033 ms、macOS 1.143792 ms，皆低於 500 ms。
這是指定 runner 的量測，不推論任意硬體皆有相同效能。

三平台固定 IR hash 為 `457ff785685189c702672e5cd6be5815a9fad1b22bb2a11f45ffddd1d3fba1aa`，
實際輸出 hash 為 `99c09682844681a9f8d9b752336f037ae8efa85fa9c551699248cceb877fd73f`。
artifact ZIP SHA-256 已與 GitHub metadata 核對：

- macOS：`757c88434be0f3bcaf2f04ce17590af6481c258e27d5f6828353039a07028828`
- Ubuntu：`e9ac10765ed5c025ecf4acd777a25baaf437e0cb9c81b6900fa431d6395c78ff`
- Windows：`fe3ed4aaea9d3b0bd71c7c39a835b9af0da009e5ba45a0dbc6154fb99f2c32e1`

下載的原始 artifacts 及彙整保存在 `dist/posix-repro/`，不提交到 Git。
此結果只覆蓋該 commit；後續程式修正須另有回歸證據。真人、付費模型及原生視窗 Gate 仍未完成。

## 持久事件版本 CI（fb1e864）

[run 44247339245](https://github.com/AllanYiin/RoutineKeel/actions/runs/44247339245)
已 completed/success，六個測試 job 均 success，publish skipped。
PR head 為 `fb1e8644a818b7c6f323ee5fe2cc1faac401217b`，實際 merge checkout
`93a7b7f258256fa869d32381933156067bd913f7`；以下涵蓋最後六項操作事件測試。

| Studio 非桌面 | passed | skipped | deselected | 秒 | 20 節點 p95 ms |
|---|---:|---:|---:|---:|---:|
| Windows 2025 | 529 | 6 | 3 | 1130.54 | 2.203 |
| macOS 15 | 529 | 6 | 3 | 142.63 | 1.38 |
| Ubuntu 24.04 | 529 | 6 | 3 | 237.94 | 2.292029 |

三份 JUnit 均為 tests=535、failures=errors=0、skipped=6。三個原生 UI 測試在 collection 後排除，
不計入 JUnit tests。每平台的 20 故障、3 個 exit(97) 程序中止與 100 次隔離執行均已逐份核對；
固定 IR／實際輸出指紋與前輪一致，三平台都已真實執行。下載 ZIP SHA-256 與 GitHub artifact metadata 相符：

- Windows：`3535803e0fa1c88f7cdbd9938f387597dc0bb93efc2653b9d90ee5afb47b57b5`
- macOS：`6543277b79e6fd46572e8ae448bc68b105a5aa3877f02a21d2c3053b31f64e5b`
- Ubuntu：`53ecf856b56f1aaf86b52f9fdb35287dbef819d1804e34943759c9848f43ed1b`

原始檔與摘要：`dist/posix-repro/*run21.zip`、`run21-summary.json`。
後續文件／入門案例提交沒有改動這輪被測的 Python；正式打包或任何後續程式變動仍需相應檢查。

## 引導工作臺與完整候選包（83a53ef）

[run 44275449294](https://github.com/AllanYiin/RoutineKeel/actions/runs/44275449294)
六個測試 job 均成功，publish skipped。PR head 為 `83a53efcca30ae5ba3fb2c0a95a646ceb18a150d`，
實際測試的 merge commit 為 `868575c95b60190e95f0752faa1f88d5e93eb917`。

| Studio 非桌面套件 | passed | skipped | deselected | 秒 |
|---|---:|---:|---:|---:|
| Windows 2025 | 572 | 8 | 3 | 1401.22 |
| macOS 15 | 573 | 7 | 3 | 140.83 |
| Ubuntu 24.04 | 573 | 7 | 3 | 229.39 |

三份 JUnit tests=580、failures=errors=0。六個 P2 影片 skip 是缺少 FFmpeg，
虛擬視窗測試在普通套件中另 skip 一項；Windows 再 skip POSIX symlink 測試。
三個原生桌面測試仍明確排除。Ubuntu 另以 Xvfb 執行一次真實 Tk 測試，1 passed／0 skipped，3.300 秒。

三平台皆實際建置 Studio ZIP，使用啟動器建立乾淨且分離的環境，再驗證重複啟動、來源案例、
試做／工程核准／發布／重開、獨立 Runtime 第二輸入及損毀拒絕。source_dirty=false，模型呼叫為 0。
同一固定 IR 與輸出 hash 在三平台相同；發行 ZIP 與驗收 artifact 的 hash 是不同層次，不混用。

| Artifact | 已核對的 SHA-256 |
|---|---|
| Windows 驗收 ZIP | `4201389a939f7863162a61c4de7c7e9cdacee00fb776f2ed33245f33de527bfe` |
| macOS 驗收 ZIP | `d251390fbeef1c152ebe384945b29135baff24b7687badf85635164e380bde07` |
| Ubuntu 驗收 ZIP | `cb9602290e2b81d59e72917e9344046eb04db51048cab2e11c6a8dbaee9fc135` |

原始檔、JUnit／安裝摘要及正常／小視窗截圖在 `dist/ui-guidance-evidence/`，
彙整為 `run27-summary.json`。截圖已檢視，確認下一步、停用說明、來源內容預覽及固定分頁。
這是 Linux 合成視窗證據，不是 Windows 真人操作，也不計入 16/20 真人驗收。
後續來源說明入口（031e001 起）仍須其對應的新版本結果。

## 來源說明與分次保存草稿（cc12c4a）

[run 44282519400](https://github.com/AllanYiin/RoutineKeel/actions/runs/44282519400)
測試 PR head `cc12c4a7a004cdc8d0dba44b82dc8343d892eaff`，實際 merge
`4cbea7f472520324a6cefb638ca6b333801ce8f7`。三平台 Studio、底座及打包六個 job 均成功，
publish skipped；三平台的原始 artifacts 均已下載並核對。

Ubuntu/macOS 各 **583 passed、7 skipped、3 deselected**；JUnit tests=590，零錯誤／失敗，
時間分別 147.795／128.102 秒。跳過範圍與前版相同。Ubuntu 獨立 Tk Gate
**1 passed、0 skipped、3.064 秒**，加入名稱起案、分次保存、缺少成功條件時仍可請 AI 釐清、
來源說明編輯與過期衝突保留；正常與小視窗合成截圖已檢視。
Windows 為 **582 passed、8 skipped、3 deselected**，JUnit 830.099 秒；多一項 symlink skip。

三平台實際 ZIP 安裝／重用／案例重播／獨立 Runtime／損毀拒絕均成功且 source_dirty=false。
已下載並對照 GitHub metadata 的 artifact SHA-256：

- Ubuntu：`a62b3a89beb229dafbf170509e938e34bfc40fb385c598064eb334a291720bbe`
- macOS：`a4b5436f25374ec79ab21784304813e42f94463db92e7810b375277a516991cd`
- Windows：`61e4659d36edcf8dad8075527cc1a10a11b0358d6d0ffa58a156566391be68e3`

證據為 `dist/ui-guidance-evidence/*-run31.zip`、`run31-summary.json`。
本機 Windows 的 cc12c4a 候選另經獨立全新安裝，證據 `dist/clarification-install-evidence/distribution.json`；
這不替代原生 Windows UI 或真人驗收。

## 完整 Tk 操作與小視窗回修（67d0a5d）

[run 44284930084](https://github.com/AllanYiin/RoutineKeel/actions/runs/44284930084)
對應 head `67d0a5dee282cf1c3a6afdc50e06796a77e13549`、merge
`3f3380c53c32687380259304bd84f58fce05cbd0`。Ubuntu 與 macOS 各
**584 passed、7 skipped、3 deselected**，JUnit 231.775／148.116 秒；兩平台實際整包安裝成功。
Windows 完整套件仍在執行。

Linux 虛擬顯示器擴充為 **4 passed、0 skipped、4.583 秒**：

- 引導／停用回饋、來源說明與過期草稿、名稱起案與分次保存。
- 真正 Tk 按鈕完成成功資料夾推導、試做、本機發布、圖片預覽及關閉重開。
- 合成串流第一段文字出現時尚未提交 Revision，完成後才保存。
- 多行文字、離開保存／取消，以及小視窗各按鈕完整寬度。

前輪 run44284483164 在已完成發布後，揭露圖片整數取整的比例斷言誤報及新專案按鈕壓窄。
比例檢查改為不放大且容許一像素取整；產品增加三欄最小可用寬度，沒有放寬按鈕寬度檢查。
四個新 JUnit case 皆非 skip；測試視窗各自清理排程，避免跨測試遺留 Tk callback。

已核對 artifact SHA-256：

- Ubuntu：`4d504bde038f698c7f8ac10d911bfae93a88f1bdf889b5b966960174f7d9ab09`
- macOS：`b7b43b5d6fa894c9de49fbfe27ca30086173421d3f12f900f3862fefd73eb1ab`

`dist/ui-guidance-evidence/run34-workflow-streaming.png`、`run34-workflow-readability-final.png`
已人工檢視。後者另顯示空流程提示沿用圖形最小寬度而裁切；後續改為依可見寬度換行並加邊界斷言，
該後續修正仍須其自己的 CI，不能算入本輪通過。
