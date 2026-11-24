# 通用工具鏈補全驗證紀錄

[技術規格](technical.md) · [分階段開發計畫](implementation-plan.md)

## Stage 0 固定基線

基線為 `c9c92b89ead033c69502cf04636df534a04b1d8a`。Windows 底座與 Studio 開發環境均為 CPython 3.12.11，封包使用 uv 0.8.8。沿用 `flow_base/uv.lock`、`workflow_studio/uv.lock`、現有 CLI／MCP stdio、工作區 SQLite 與 Studio 獨立資料。測試工作區、宿主觀測、原生封包與執行收據留在暫存位置。

報表固定測具為 `tests/fixtures/toolchain_completion/report_case.json`。兩份 CSV 與獨立預期值從技術規格的 A9 取樣。基線測試證明，同一草稿的指定能力未在受信清冊時保留缺口，觀測到完全相符的工具後能使用原指定綁定。測具的觀測只證明解析，不替工具行為背書。

| 核對項目 | 結果 | 證據 |
| --- | --- | --- |
| 底座資格、補強、Graph 安裝閘門、宿主派送與真實 MCP stdio 轉本機 CSV | 70 通過 | `dist/toolchain-stage0-base.xml` |
| Studio Pattern 合成、翻譯審查與迴圈 | 10 通過 | `dist/toolchain-stage0-studio.xml` |
| Graph 契約、v1 及巢狀安裝、SQLite 備份還原與新封包 | 55 通過 | `dist/toolchain-stage0-install-fresh.xml` |
| Studio 資料升級及舊格式實際還原 | 10 通過 | `dist/toolchain-stage0-studio-migration.xml` |
| 報表的缺口與原指定綁定 | 1 通過 | `dist/toolchain-stage0-report.xml` |
| Ruff、Studio 格式及 Git 空白 | 通過 | `ruff check`、188 個 Studio 檔案已符合格式、`git diff --check` |
| 具名候選與 ZIP 碼完整性 | 五份通過 | `dist/candidate-toolchain-stage0-fresh/release/SHA256SUMS` |
| 兩套隔離 wheel 實際交付 | 通過，`run_5dcca30a476a45e79355eca46c085e25` | `dist/toolchain-stage0-wheel-smoke-final/smoke-evidence.json` |

### 重跑

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) 'dist/toolchain-uv-cache'
& flow_base/.venv/Scripts/python.exe tools/release.py --candidate toolchain-stage0-fresh --reference-packs
$env:ROUTINEKEEL_TEST_RELEASE_DIR = Join-Path (Get-Location) 'dist/candidate-toolchain-stage0-fresh/release'
$env:ROUTINEKEEL_E2E_ENFORCE = '1'
& flow_base/.venv/Scripts/python.exe -m pytest -q tests/test_capability_resolver.py tests/test_capability_binding.py tests/test_binding_qualification.py tests/test_blueprint_completion.py tests/test_graph_builder.py tests/test_graph_fault_matrix.py tests/test_graph_review_external.py tests/e2e/test_pattern_portability.py
& flow_base/.venv/Scripts/python.exe -m pytest -q tests/test_db_migrations.py tests/test_blueprint_install.py tests/test_nested_blueprint_install.py tests/test_release_layout.py tests/test_graph_contracts.py tests/test_toolchain_completion.py::test_pinned_report_surfaces_gap_then_resolves_observed_exact_tool
& workflow_studio/.venv/Scripts/python.exe -m pytest -q tests/studio/test_pattern_composition.py tests/studio/test_translation_review_rwp.py tests/studio/test_translation_review_loop.py tests/studio/test_migrations.py tests/studio/test_pattern_registry.py::test_v15_backup_can_restore_in_previous_schema
```

若具名候選已存在，使用另一個候選名稱並同步更新 `ROUTINEKEEL_TEST_RELEASE_DIR`。初次封包測試指到先前留存的 ZIP，有三項版型差異。已用目前原始碼建立新候選並完整重跑。預設 uv 快取無法寫入，本輪改用工作區 `dist/` 下的專用快取。

146 項測例均通過。隔離驗收使用 `dist/toolchain-stage0-installed/.venv` 和 `.studio-venv`，將本次建出的 wheel 安裝到各自的環境。第三方依賴沿用先前驗證過的套件，兩環境通過 `uv pip check`。已依目前 Studio 契約補入分段確認證據，實跑匯出、候選驗證、安裝及 ZIP 交付。

本階段只增加固定測具及驗證紀錄。回退範圍為新增的測具與基線文件。各遷移測試在拋棄式資料庫驗證備份與還原，私人已安裝環境未納入。Windows 沙箱回報 `Wsl/E_ACCESSDENIED`，Linux 基線與後續 Stage 6 平台實跑需在可用 Linux 環境取得。

## Stage 1 通用需求與候選診斷

Graph 2.3 一般與巢狀 Task 可宣告 `capability_requirement`。和 Pattern 共用需求模型、節點路徑、資格收據及環境鎖。未宣告需求的 v1 與舊 Graph 照原指定工具解析。底座的補強修訂保存快照識別與候選淘汰理由。受信來源恢復後以原工作 ID 重驗，仍須取得適用的資格證據。Studio 的初稿校閱與預檢展示同一份需求。

| 核對項目 | 結果 | 證據 |
| --- | --- | --- |
| 底座、CLI／MCP、資格、隔離安裝與外部核准 | 159 通過，0 跳過 | `dist/toolchain-stage1-runtime.xml` |
| Studio 合成、擷取、資格、Registry 與翻譯回審 | 28 通過，0 跳過 | `dist/toolchain-stage1-studio.xml` |
| Ruff、Studio 格式及 Git 空白 | 通過 | `ruff check`、188 個 Studio 檔案已符合格式、`git diff --check` |

固定報表測具涵蓋候選版本不符、輸出語意不符、未授權、觀測過期與同一補強工作重驗。巢狀 ForEach 使用一般節點要求，在無 Pattern 的情況實際完成資格鎖定、候選試做、安裝與執行。另以兩條真實 MCP stdio 連線核對同名工具各自的派送名稱與來源證據。相同路徑的 Pattern 和一般需求衝突時拒絕匯入。

本階段未調整 SQLite 格式。回退時保留既存藍圖、補強工作與資格收據。若要套用舊底座，新 Graph 2.3 藍圖改回可在舊底座驗證的具名實作與藍圖修訂，再走原候選流程。舊的 Graph 版本仍可直接由沿用的入口執行。Linux 及最終發行包重播由 Stage 6 驗收。

## Stage 2 多工具搜尋、資格與正式候選

`blueprint_completion` 的能力缺口現在可列出具型別與語意的多工具提案，藍圖仍保持待補強。固定報表測具在無單一工具時，搜得讀取、正規化、勾稽三步。編譯器以目前工具指紋與資料埠重建 Graph；在獨立測試工作區由 Graph Scheduler 逐步派送給本機測具並取得既有形式的宿主回執。產物符合固定案例，整鏈超過成本上限時未列為候選，達到搜尋上限時回報明確狀態。六項測例通過，見 `dist/toolchain-stage2-planning.xml`。

同一版程式重跑 40 項規劃、補強、單工具選擇、Graph 建置與實際 MCP stdio 轉本機 CSV 測試，全數通過，見 `dist/toolchain-stage2-compat.xml`。Ruff、Studio 格式和差異空白檢查通過。

搜尋達限時保存目前狀態、候選與游標，續做時核對原需求、工具與環境觀測。測試以同一補強工作跨服務重開逐片搜尋，並驗證環境觀測變動後重建搜尋。宿主提供的步驟與資料邊經底座重建，未觀測的工具被拒絕。已選工具鏈的資格、環境鎖、候選包與正式執行使用相同編譯圖。

| 核對項目 | 結果 | 證據 |
| --- | --- | --- |
| 底座、資格、候選、既有流程及真實 MCP stdio 相容回歸 | 126 通過，0 跳過 | `dist/toolchain-stage2-final-runtime.xml` |
| 最後的整鏈工作量、父子待辦與核准回歸 | 33 通過，0 跳過 | `dist/toolchain-stage2-meter.xml` |
| 本機與宿主工具鏈安裝後實跑 | 2 通過，0 跳過 | `dist/toolchain-stage2-deployed.xml` |

本機鏈以兩組已確認案例取得資格，隔離試做無宿主替身，再正式安裝並執行。撤銷其中一個工具的權限後，原鎖定回報 `BINDING_STALE`。宿主鏈先核對每步來源回執和已確認結果，再以替身完成隔離候選驗收，安裝後透過受控待辦執行本機測具。正式外部服務與費用另依 Stage 6 的授權範圍驗收。

本階段不變更 SQLite 版本。停用多工具規劃可保留既有單工具綁定。回退需選回先前合格的已安裝套件與鎖定；本階段發出的工具鏈候選須由支援此綁定格式的底座執行。回退時保留原藍圖、候選收據、執行歷史與搜尋檢查點。

## Stage 3 受信套件及宿主配置

受信工具清冊的 wheel 配方須與能力、工具、平台及套件版本相符。底座先保存操作識別與狀態，核對來源及校驗值，再使用 uv 在工作範圍內的暫存位置離線安裝。驗證套件清單、傳遞相依、具名入口與站點指紋後，才以原子更名發布準備收據。重送同一準備只讀回原收據。已準備的套件以獨立程序執行資格案例，候選試做另複製及重驗環境。這條路徑使用已受信的 wheel。

兩個案例已通過，見 `dist/toolchain-stage3-chains.xml`。單節點案例從未安裝的 wheel 開始，完成準備、資格、候選安裝及執行。兩工具案例先找出缺少的正規化工具配方，準備後重新規劃，試做並安裝正規化與 CSV 讀取工具鏈。兩個案例均確認重送保留相同收據，校驗錯誤的配方無收據，已安裝檔案更動後原綁定為 `BINDING_STALE`。

已在隔離子程序內實際寫入測試服務設定，並使用新觀測取得工具資格、建立候選及安裝。並行領取同一配置只回傳一次派送；第二個工作無法重複配置同一服務。模擬在取得回執前失聯，以及在已查證、尚未保存工作修訂時中斷，均依原操作 ID 復原。原配置及補償各寫入一次。經證實未執行後，後續嘗試須用新範圍另行授權。另驗證工具鏈可為尚未可呼叫的前置工具提出配置待辦。

| 核對項目 | 結果 | 證據 |
| --- | --- | --- |
| 離線準備、宿主配置、補強、資格、外部回執與診斷回歸 | 47 項通過，原缺少 `ADAPTER_INPUT_REJECTED` 診斷的 1 項補齊後通過 | `dist/toolchain-stage3-final.xml`、`dist/toolchain-stage3-catalog.xml` |
| 服務範圍防重、查證後重開、補償及多工具前置配置 | 2 項通過，0 跳過 | `dist/toolchain-stage3-host-fence.xml` |

本階段不變更 SQLite 版本。回退時保留已準備環境、操作回執與工作修訂，選回相符的工作流及鎖定。宿主設定的回復須走具名補償及獨立授權。驗收使用本機隔離測具，未配置真實外部服務或產生費用。

## Stage 4 Adapter 建置、隔離與獨立驗收

依受信配方建置並封存樹狀資料映射或 WebAssembly 程式。資料映射採具名埠及 JSON Pointer，逐列輸出保持來源順序。WebAssembly 在獨立 Node 程序中驗證無宿主匯入、有效的 Adapter 介面、記憶體上限及程式與執行器指紋。驗收案例使用原藍圖保存的預期值。候選試做複製建置產物後重驗，安裝前及正式執行仍依鎖定指紋核對。

Documa 實際解析 DOCX、搜尋並傳入建成的映射 Adapter，正式安裝後交付文件來源引用。映射缺少必要欄位時，獨立反例核對預期錯誤回傳。程式 Adapter 以兩組獨立案例取得資格，候選安裝後可執行；未宣告參數、超出記憶體上限、獨立預期不符及內容變更時，驗收或執行會阻擋。文件驗收案例不再要求使用者提供底座負責派生的 `document_indexes`。

50 項建置、Documa、宿主配置、資格、候選及診斷回歸全數通過，0 跳過，見 `dist/toolchain-stage4-final.xml`。回退保留 Adapter 產物、資格收據及已交付結果，選回前一個通過驗證的工作流候選及其對應工具環境。未建置或無法重現隔離證據的程式配方保持待處理。

## Stage 5 執行失效、重綁與續做

已安裝工具鏈的執行測例在領取宿主步驟前撤銷工具權限。Runtime 停止受影響派送，保留既有待辦，依原套件的藍圖建立同一補強工作。重新查詢仍指向原工作及 Run。恢復同一工具與權限後，治理狀態回到原 Run，原待辦才可派送。已重現配置重綁源檔時的套件鎖衝突，改由內容定址的原藍圖草稿復原，保留原套件與資料。

等效替代使用同一 Builder、隔離試做及安裝驗證。Runtime 將已驗證的套件保存在同一工作範圍內，留下原 Run、原套件與原綁定證據。續做 Run 使用新候選修訂並連回來源 Run，工作佇列維持同一識別。可驗證的輸入、成果與人工校閱由續做 Run 沿用。若取得外部回執後才能確認結果，Runtime 等候查證。重用外部效果須有對應的原授權、請求與結果證據。若巢狀步驟受替換影響且無法證明其中既有外部效果可安全沿用，Runtime 留下阻礙並暫停派送。

| 核對項目 | 結果 | 證據 |
| --- | --- | --- |
| 失效偵測、原綁定復原、宿主派送與迭代 | 27 項通過，0 跳過 | `dist/toolchain-stage5-rebind.xml` |
| 等效替換、佇列、交付、既有圖形與候選回歸 | 64 項通過，0 跳過 | `dist/toolchain-stage5-final.xml` |
| 最新續做檢查點與範圍驗證 | 2 項通過，0 跳過 | `dist/toolchain-stage5-seal.xml` |

分別在保留續做 Run 前、保留後尚未寫入檢查點，以及啟用後尚未派送時中斷，重試只啟動同一個續做 Run。測例亦涵蓋啟動前發現的過期綁定、重複查詢、外部回執不明與副作用無法安全沿用。回退保留原 Run、補強工作及候選證據，並恢復相符的已驗證工具環境。正式外部服務的授權與查證由相應宿主負責。

## Stage 6 整合與發行候選驗收

報表測具使用可離線準備的真正 wheel，清冊分別登錄 CSV 讀取、精確小數正規化及逐鍵比較。A2 從三個配方都尚未安裝開始，建立單一需求的三步工具鏈，使用兩組獨立案例通過試做後安裝，交付固定差異報表。Windows 與 Linux 各一例通過，見 `dist/toolchain-stage6-a2-win.xml`、`dist/toolchain-stage6-a2-linux.xml`。

A9 以兩份相同的 CSV 實跑已安裝流程，在讀取結果保存後撤銷原正規化綁定。Runtime 建立補強工作，重新搜尋並準備替代工具，第二份候選試做通過後建立續做 Run。原套件保留，原讀取成果及校閱可沿用，重送啟用仍回到同一續做 Run。Windows 原 Run `run_fd1a27d85e7a4befb1d9233acf309a05`、續做 Run `run_6cb615bbb156402d862311da5742ae8f`，同屬工作 `a4c0353dd304d09400da3d204bf57330`。初次與換綁的驗收建置分別為 `36721444cb7a45ddb8b5440c3af3d016`、`83469a67fe1140ea9c8aa99ebb669c1e`。讀取結果指紋在兩次 Run 一致。最終結果為 `K01` 相符、`K02` 差 `0.10`、`K03` 僅在右表。

此案例重現兩個缺口。已鎖定的工具鏈失效後，草稿補強須另搜尋可用方案，安裝包仍按原鎖定判定。續做選取可沿用成果時，以個別綁定、資格收據與依賴的指紋核對，新的整體環境鎖識別仍須通過候選及啟用驗證。CLI 新增可實際呼叫的 `preview_rebind` 與 `activate_rebind`。Windows 的 CLI 重播與 Linux 的相同案例已驗證，見 `dist/toolchain-stage6-a9-lifecycle.xml`、`dist/toolchain-stage6-linux-replan.xml`。

| 核對項目 | 已取得結果 | 證據 |
| --- | --- | --- |
| Linux 發行候選內執行 CLI、真實 MCP stdio、Studio、舊版安裝與回退、SQLite 還原 | 修正候選 167 項通過，0 跳過。包含 Linux 程序的 TCP 監聽探測、宿主配置、工作區搬移及交付回退 | `dist/toolchain-stage6-linux-final-packages.xml` |
| Linux 修正候選重跑換綁、離線準備、WASM、宿主斷線及 MCP stdio | 16 項通過，0 跳過 | `dist/toolchain-stage6-linux-replan.xml` |
| Linux Studio | 首輪 566 項通過。以拋棄式 Linux 環境補入 FFmpeg／ffprobe 6.1.1，影片測試檔 8 項通過，覆蓋首輪暫缺的 6 項 | `dist/toolchain-stage6-linux-studio.xml`、`dist/toolchain-stage6-linux-video.xml` |
| Windows 首輪底座回歸與修正 | 185 項通過，4 項需重驗。離線準備及案例斷言改在工作區內的拋棄式目錄重跑，3 項通過。宿主斷線後驗證沒有派送，原待辦和 Run 在還原連線後可續做，1 項通過 | `dist/toolchain-stage6-win-base.xml`、`dist/toolchain-stage6-win-diagnose.xml`、`dist/toolchain-stage6-disconnect.xml` |
| Windows 候選隔離環境 | 146 通過，1 項並行讀取試做進度時遇到檔案共用衝突。修正後原失敗、進度讀寫隔離及獨立預期值 3 項通過，0 跳過 | `dist/toolchain-stage6-win-isolated.xml`、`dist/toolchain-stage6-win-progress.xml` |
| Windows Studio 與最後的續做安全回歸 | Studio 572 項通過，另以隔離 Studio 重播 32 項。續做及宿主配置 6 項通過；拒絕重複外部效果時原 Run 可操作。Linux 相同續做安全測例 2 項通過 | `dist/toolchain-stage6-win-studio.xml`、`dist/toolchain-stage6-win-isolated-studio.xml`、`dist/toolchain-stage6-windows-final-rebind.xml`、`dist/toolchain-stage6-linux-continuation-safety.xml` |

Windows 與 Linux 各建立具名候選及分開的底座、Studio 環境。實測 CPython 分別為 3.12.11 與 3.12.3。兩平台各五份 ZIP 通過 SHA-256，兩平台的獨立環境通過 `uv pip check`。試做暫時無法存取進度檔時，CLI 與 MCP 仍可查詢持久化工作狀態，正式驗收與安裝依原案例收據判定。首輪失敗及缺少依賴的案例均另有重跑證據。

| 規格 | 重跑測例與證據位置 |
| --- | --- |
| A1 | `tests/test_toolchain_completion.py` 的一般與巢狀需求，`tests/test_blueprint_install.py`、`tests/test_nested_blueprint_install.py` 的相容回歸。 |
| A2 | `tests/test_toolchain_lifecycle.py::test_plans_and_runs_one_three_step_adapter_chain`，`dist/toolchain-stage6-a2-win.xml`、`dist/toolchain-stage6-a2-linux.xml`。 |
| A3 | `tests/test_adapter_construction.py::test_constructed_mapping_delivers_documa_references`，`dist/toolchain-stage4-final.xml`、`dist/toolchain-stage6-win-documents.xml`。於 Windows 執行實際 Documa 的 PDF／DOCX 查詢、生成引用映射並安裝。 |
| A4 | `tests/test_toolchain_completion.py` 的拒絕原因、成本與搜尋上限，`tests/test_blueprint_completion.py::test_declining_a_host_adapter_keeps_independent_case_work` 的獨立補件驗證。 |
| A5 | `tests/test_host_preparation.py` 的配置搶占、重送與補償，`tests/test_blueprint_completion.py` 的試做重啟，`tests/test_toolchain_preparation.py` 的套件內容漂移。 |
| A6 | `tests/test_toolchain_completion.py` 的原綁定復原，`tests/test_continuation.py` 的中斷復原、單一續做 Run、佇列結果及沿用指紋。 |
| A7 | `tests/test_graph_review_external.py` 和 `tests/test_continuation.py` 的授權及獨立回執，`tests/test_workflow_updates.py` 和 `tests/test_atomic_delivery.py` 的竄改拒絕、回退與已安裝流程實跑。 |
| A8 | `dist/toolchain-stage6-linux-final-packages.xml`，Windows 與 Linux 具名發行候選及隔離環境。 |
| A9 | `tests/test_toolchain_lifecycle.py::test_installed_three_tool_workflow_resumes_after_normalizer_drift`，本節所列的工作、Run、驗收建置及輸出。 |
| A10 | `tests/test_host_preparation.py::test_host_setup_recovers_without_redispatch_and_compensates`，`dist/toolchain-stage3-host-fence.xml`、`dist/toolchain-stage6-linux-final-packages.xml`。 |
| A11 | `tests/test_adapter_construction.py::test_constructed_wasm_is_verified_installed_and_rechecked`，`dist/toolchain-stage4-final.xml`、`dist/toolchain-stage6-linux-replan.xml`。 |

## Stage 7 本機候選與操作指引

底座 `blueprint-install.md` 說明如何比較候選、使用已授權的離線配方、讀取原試做收據、處理 `REBIND_REQUIRED`、核對續做範圍及回退。Studio `docs/host-conversation.md` 說明如何在定稿保存共用能力與案例契約，以及如何向使用者呈現底座的缺口與核准範圍。兩份套件的入口 `AGENTS.md` 隨包更新。

`dist/` 下的最終候選為 `candidate-toolchain-final-windows` 與 `candidate-toolchain-final-linux`。各候選包含底座、Studio、兩個既有流程、完整工作區 ZIP、版本清冊及 SHA-256。參考領域包及 Data Adapter 在 `domain-packs/`，持有各自的 `SHA256SUMS`。各平台執行環境分開安裝底座和 Studio wheel，並用 `uv pip check` 核對。Windows 最終候選 28 項通過，Linux 最終候選 119 項通過，兩組均 0 跳過，見 `dist/toolchain-final-windows.xml`、`dist/toolchain-final-linux.xml`。

兩平台各 5 份發行 ZIP 及 5 個領域包項目均通過 SHA-256。逐一核對底座操作指南、底座入口、Studio 宿主指南及 Studio 入口在獨立 ZIP 和全包的內容一致。使用最終 wheel 查驗 `blueprint-task` 的 `preview_rebind`、`activate_rebind` 和 Studio `guide`。最終案例重跑實際涵蓋三工具規劃、同工作續做、封包版型、CLI／MCP、既有流程回退，以及資料庫還原。

在儲存庫根目錄執行 `python tools/release.py --candidate <新候選名稱> --reference-packs`。以本次候選的 `release/` 設定 `ROUTINEKEEL_TEST_RELEASE_DIR`。在兩個獨立環境安裝所建的底座和 Studio wheel，並以 `ROUTINEKEEL_STUDIO_PYTHON` 指定候選的 Studio Python。用底座環境重跑 `tests/test_toolchain_lifecycle.py`、`tests/test_continuation.py`、`tests/test_release_layout.py`、`tests/test_mcp_stdio.py`、`tests/test_workflow_updates.py` 與 `tests/test_db_migrations.py`。底座與 Studio 的維護文件均可從獨立 ZIP 或全包取得。

候選供本機操作與回退驗收。正式環境的外部帳號、付費服務、私人已安裝工作區及其他平台未納入這輪測試。Documa 解析與引用採 Windows 本機實測，Linux 的程式 Adapter 採原生 Node/WASM 驗證。
