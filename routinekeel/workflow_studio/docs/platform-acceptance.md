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
