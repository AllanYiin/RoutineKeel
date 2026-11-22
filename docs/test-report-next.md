# RK-NEXT 本機整合驗收

本報告以 `01_Technical_Spec.md` 及 `03_Codex_Stages.md` 的 RK-NEXT 規格為準。驗證使用獨立的套件目錄與臨時資料工作區。正式發行及使用者已安裝的流程均未參與測試。

## 驗收狀態

| 項目 | 執行環境 | 狀態與證據 |
| --- | --- | --- |
| 新版比較、中斷恢復與回退 | Windows | PASS。4 項演進專項、83 項底座回歸、104 項 Studio 回歸、29 項串流測試。原始報告在 `dist/v122-stage13-*.xml`。 |
| Agent 工具、排隊、關鍵校閱及跨連線續編 | Windows，實際 MCP stdio 用戶端與 CLI | PASS。22 項通過，0 跳過。`dist/v122-stage14-verified.xml`。 |
| 底座與工作流全庫測例 | Windows、本機 WSL、demucs-cpu | 656/656 個唯一測例均有 PASS。Windows 首輪 619 passed、6 failed、30 skipped。新增冷啟動程序身分測例後，以完整參數名稱對照修復重跑與跨環境報告，缺漏為 0。`dist/v122-stage15-runtime-coverage.json`。 |
| Studio 原始碼、測試與腳本的 Ruff 檢查 | Windows | PASS。依 CI 相同設定執行 lint 及 format，164 個檔案已格式化。 |
| 全庫 Ruff | Windows，Ruff 0.17.5 | PASS。`ruff check .` 涵蓋 353 個檔案，最終 JSON 診斷清單為空。 |
| POSIX 啟動、檔案權限與復原 | 本機 Ubuntu WSL | PASS。清理後候選在 WSL 原生暫存目錄、標準 Linux PATH 下，23 項全數通過，PowerShell 7.4.13 冷啟動 1.19 秒。保留先前從 Windows 掛載目錄執行時的 12 個啟動逾時。兩次均使用原測試期限。`dist/v122-stage15-checked-posix.xml`、`dist/v122-stage15-posix.xml`。 |
| 本機 Linux 工作生命週期及發布故障 | WSL 原生檔案系統，Runtime／Studio 分別安裝 | PASS。31 項 Service、生命週期與中斷復原，以及 20 種已發布工作流故障注入，合計 51 項全數通過。`dist/v122-stage15-native-runtime.xml`、`dist/v122-stage15-native-studio.xml`。 |
| Studio 全套 Linux | 本機 WSL，CPython 3.12 | 528 passed、6 skipped，557.64 秒。6 項皆因該發行版未安裝 FFmpeg／ffprobe，與 Windows 補驗逐名對照。`dist/v122-stage15-native-studio-full.xml`。 |
| Studio 全套 Windows | Windows，CPython 3.12 | 534/534 個唯一測例均有 Windows PASS。首輪 521 passed、13 failed，6015.77 秒；13 個案例在 Windows 本機新目錄重跑，原驗證期限下全部通過。WSL 缺少媒體工具的 6 項另在 Windows 重跑，6 passed。逐案對照見 `dist/v122-stage15-studio-windows-coverage.json`。 |
| 字幕降噪選配環境 | 本機 demucs-cpu | PASS。清理後 7 項通過，詳見 `dist/v122-stage15-checked-denoise.xml`。 |
| 全新 ZIP 第一次使用 | Windows | PASS。五份 ZIP 的 SHA-256 核對成功。在新目錄解壓完整包、透過啟動器建立各自的環境，再用包內 Python 讀到兩個已安裝流程，並向包內 Studio 建立專案、取得訪談問題。`dist/v122-stage15-cold-smoke.json`。底座使用 `start.ps1 list`；`--prepare-only` 是 Studio 啟動器參數。 |
| 發行包的 MCP 入口 | Windows，本機標準輸入／輸出 | PASS。使用該包的 `--install-mcp` 安裝選配依賴，從獨立 SDK 用戶端完成協議連線、列出 32 個工具，讀回 CLI 建立的同一份訪談及其問題。`dist/v122-stage15-cold-mcp.json`。 |
| macOS、遠端 CI | 未連接外部執行環境 | 待驗收。使用者指定此次驗證限本機。 |
| 非技術使用者走查 | 真實桌面對話 | 待最後驗收。自動測例中的回覆只作為測試資料。 |
| 付費模型、真實對外發布 | 未執行 | NOT_RUN。工程案例使用本機資料或隔離的宿主回執。 |

## 核心路徑與故障證據

`tests/test_routinekeel_e2e.py` 從空工作區建立訪談，記錄答覆，暫停、續編及匯出資料藍圖。底座建置候選套件，在臨時工作區載入並執行測例。之後以同一份驗證收據正式安裝到測試工作區，透過 MCP 提交與重送、Worker 執行、完成關鍵校閱、提供修正、比較候選與回退。

| 情況 | 對應測試 |
| --- | --- |
| DAG、兩條選路、匯合、集合、有界迭代、多個交付端、拒絕校閱 | `tests/test_graph_fault_matrix.py`。每組圖形、輸入及預期結果的固定 SHA-256 保存於 `tests/fixtures/rk_next_corpus.json`。 |
| 程序中斷、斷線、重送、過期版本 | `tests/test_graph_scheduler.py`、`tests/test_graph_iterations.py`、`tests/test_agent_workflow_facade.py`、`tests/test_workflow_evolution.py`。 |
| 對外操作已領取而結果未知、工具缺失或權限改變 | `tests/test_graph_review_external.py`、`tests/test_graph_fault_matrix.py`。不會因重連而重送。 |
| 輸入資料、來源包路徑、Golden Case 或必要驗證器無效 | `tests/test_blueprint_install.py`、`tests/test_graph_contracts.py`、`tests/test_graph_fault_matrix.py`。失敗建置保存草稿。 |
| v1、舊 Studio 發布、回退及資料還原 | `tests/test_blueprint_recovery.py`、`tests/test_quality_feedback.py`、`tests/test_db_migrations.py`、`tests/studio/test_migrations.py`、`tests/studio/test_release_recovery.py`。 |

## 重跑方式

從儲存庫根執行。先讓 `ROUTINEKEEL_TEST_RELEASE_DIR` 指向本次隔離建出的 `release` 資料夾，同一層需有 `workspace` 及 `wheels`。當平台或選配套件的條件不符，保留 pytest 原始的 skip 原因，並對照補跑報告中的測例名稱與完整參數。

```powershell
uv run --locked --python 3.12 --project flow_base --extra mcp --group dev --with-requirements dist/subtitle-test-requirements.txt python -m pytest tests workflows --ignore=tests/studio -q -rs --junitxml=dist/v122-stage15-runtime.xml
uv run --locked --project workflow_studio --group dev python -m pytest tests/studio -q -rs --junitxml=dist/v122-stage15-studio.xml
uv run --locked --project workflow_studio --group dev ruff check .
uv run --locked --project workflow_studio --group dev ruff check --config workflow_studio/pyproject.toml workflow_studio/src workflow_studio/scripts tests/studio
uv run --locked --project workflow_studio --group dev ruff format --check --config workflow_studio/pyproject.toml workflow_studio/src workflow_studio/scripts tests/studio
```

## 靜態檢查與相容性

第一次掃描底座、流程和測試得到 568 則診斷。依要求擴及整個儲存庫後，納入了啟動器與封裝腳本。共用 pytest fixture 改由同層 `conftest.py` 提供，仍保留原測例和斷言。修正例外處理中延遲取用例外變數的寫法，並讓診斷寫入及回報失敗可以記錄自身狀態，保留原始工作結果。CLI、插件程序和驗證器的最外層仍須將未知錯誤回報給呼叫端，這些邊界的目的已在程式中說明。

清理後第一輪完整收集發現字幕模組的 `resource_path` 轉出缺失，四個模組因此無法載入。已恢復公開名稱，再用同一來源核對其呼叫端；重新收集得到當時底座與工作流 655 項、Studio 534 項。新增慢啟動程序身分測例後，底座與工作流共 656 項。收集成功與實際執行結果分開記錄。

底座與 Studio 的受控語言實作有一處相同運算式的排版差異；同步格式後，兩份檔案全文比對及候選驗證均通過。另實測冷啟動時的程序識別空窗：底座取得 OS 建立的子程序後即記下 PID 和程序出生時間。工作狀態由實際執行端接手後才轉換。慢啟動、程序死亡、PID 重用、重送及取消均以持久化資料核對。並行測例把就緒訊號與後續驗收分開，仍檢查真正的子程序、相同 Run ID、取消通知期限與完整交付。

字幕環境中的第三方 `ctranslate2` 匯入 `pkg_resources` 時產生一項棄用警告。Windows 的測試環境亦曾在程序結束時由第三方 `pyreadline3` 回報已關閉的主控台 handle。完整收集程序的退出碼為 0。兩者均保留原始輸出，供下次升級依賴時核對。

## 資料復原

版本回退憑更新編號使用底座的檔案備份。資料庫降版或完整工作區還原需停寫，將 SQLite 備份、對應套件與成果同時還原到另一個空目錄，核對版本、完整性與代表性資料後才重新啟用。回退不會重新派送外部操作。

## Stage 16 本機候選交付

候選目錄為 `dist/candidate-rk-next-0.3.0-rc1/release/`。底座與 Studio 使用 0.3.0。兩個參考流程與選配媒體套件均使用新的修補版本，以便現有安裝辨別套件內容。實際版本為字幕 0.5.1、複製 0.2.4、媒體 0.1.1。對話契約與資料庫升級細節見[維護與打包](maintenance.md)。

完整檔案 `RoutineKeel-workspace-0.3.0.zip` 共 3,703,888 bytes，SHA-256 為 `205f36ebce995feee0c3bc9fcb83aaf7ddf50ae81ad16540f9fb2e7b73b4dc6c`。其餘四個分包的大小、版本與指紋列於同目錄的 `versions.json` 及 `SHA256SUMS`。

- 五個 ZIP 的 CRC 與 SHA-256 通過。六個 wheel 的 RECORD、模組內容及來源逐檔比對通過。既有發行目錄仍符合原校驗清單。
- 原始碼的 66 份文件檢查 56 個本機連結，解壓工作區的 52 份文件檢查 23 個本機連結，缺漏均為零。
- 在全新工作區解壓最終 ZIP，只預置先前下載的公開依賴快取及 CPython。實際啟動器重新建立底座、Studio 與 MCP 環境。從包內 stdio 實測訪談、計畫、提交、獨立 Worker、重連及成果讀取。
- 使用既有複製流程 0.2.3 的分包實際執行，再以最終 0.2.4 ZIP 更新並執行，最後依更新收據回復到 0.2.3 後重跑。三次取得獨立 Run，內容均與輸入相同。
- 發行包相關的底座、更新、遷移、對話與整合回歸共 106 項通過，0 跳過。`dist/v122-stage16-runtime.xml`、`dist/v122-stage16-package-accept.json`。
- Studio 的分包、藍圖匯出、發布中斷及回復測試 31 項通過，0 跳過。`dist/v122-stage16-studio.xml`。包含本階段新增的交付檢查後，累計 659 個底座與流程測例及 534 個 Studio 測例，每項都有本機 PASS 紀錄。
