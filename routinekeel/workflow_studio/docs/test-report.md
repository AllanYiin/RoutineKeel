# Studio 驗證紀錄

## 起始環境（2026-11-17）

Windows，CPython 3.12.11；本機 checkout `2397ede`，另含既有未提交變更。
變更前完整基線：383 passed、1 failed、21 skipped，197.82 秒。
既有失敗為 `test_recovery_launch_skips_model_preparation`：未提交的 launch
白話錯誤修正輸出文字，但既有測試 helper 仍將 stdout 當 JSON。Studio 未改此路徑。

`uv run` 在此 sandbox 先遇到預設 cache ACL，再遇到 pytest launcher
`Failed to canonicalize script path`。安全替代為既有環境直接執行模組：

```powershell
& routinekeel/flow_base/.venv/Scripts/python.exe -m pytest tests routinekeel/flows -q
& routinekeel/flow_base/.venv/Scripts/python.exe -m pytest tests/studio -q
```

uv cache 可指定為工作區 `dist/uv-cache`，不變更任何安全控制。
macOS／Linux 與真人 UI 尚未執行；舊底座歷史 CI 不能算成 Studio 驗證。

## Stage 0

- `flow_base/.venv/Scripts/python.exe -m pytest tests/studio tests/test_baseline_contract.py tests/test_decoupling.py -q`
  （路徑前綴 `routinekeel/`）：12 passed，3.85 秒。
- Studio 專屬環境 `python -m pytest tests/studio -q`：3 passed，0.10 秒。
- `python -m ruff check --config routinekeel/workflow_studio/pyproject.toml routinekeel/workflow_studio/src tests/studio`：通過。
- 相同範圍 `ruff format --check`：8 files already formatted。
- `uv build routinekeel/workflow_studio --out-dir dist/studio`：sdist／wheel 通過。
  sandbox canonicalize 拒絕後，同一命令經環境授權執行成功。
- wheel 以 `uv pip install --no-deps --target dist/studio-installed` 安裝，隔離模式
  `python -I` 從該目錄匯入並初始化服務，回報版本 0.1.0／schema 0。
- 未改動底座公開契約、既有 staged `.idea` 或啟動訊息修正。
- 原始需求 SHA-256：`2322d09b209ef08accc471f722c97c602226b5d66a14b3d9c9a743cbb1365378`。

回滾：撤回 Stage 0 新增套件與測試即可；此階段沒有持久化使用者資料。

## Stage 1

- Studio 專屬環境 `python -m pytest tests/studio -q`：24 passed，1.28 秒。
  覆蓋 CRUD／重開、同名專案隔離、CAS／重複提交、不可變發布版本、草稿捨棄、
  100 次建立重開、故障交易、程序退出、遷移重跑、備份／還原、較新版本及損毀拒絕。
- 底座環境 `python -m pytest tests/studio tests/test_baseline_contract.py tests/test_workflow_service.py tests/test_decoupling.py -q`：
  44 passed，19.32 秒（之後新增兩個儲存拒絕測試已於 Studio 24 項全測通過）。
- Ruff check 通過；格式化範圍限 Studio 與其測試。
- Stage 0 commit：`5543ebd`。本階段沒有修改 Runtime 契約或 Runtime DB。
- 回滾／備份實測步驟見 migration-strategy.md；既有基線失敗保持單列。

## Stage 2

- Studio 專屬環境 `python -m pytest tests/studio -q`：54 passed，2.03 秒，0 skipped。
- 包含實際 PNG／JPEG／WebP 解碼、等比尺寸、100 檔資料夾、ZIP 穿越／連結／膨脹／
  重複路徑、空白／損壞來源、跨專案拒絕、原始檔保留、來源歷史、交易回復。
- Windows ZIP 原始檔名正規化缺口已修復並回歸驗證。
- v001→v002 實測保留專案，升級前備份還原後仍是 v001 並可讀取原專案。
- Studio 增加 Pillow 13.0.0（lockfile 固定）；未增加 Runtime 依賴。
  底座單獨環境未安裝 Pillow 時，Studio 子套件測試明確 skip，須另外使用 Studio 環境執行；
  此處 54 項是實際執行結果，不以 skip 算作通過。
- Stage 1 commit：`b525708`；Stage 2 來源副本／預覽與清單設計見 source-formats.md。
- Stage 2 最後回歸：底座環境執行 Studio／baseline／decoupling 為 33 passed、3 skipped
  （Pillow 不在底座環境，三個來源測試模組明示跳過）；Studio 獨立環境重跑為 54 passed、0 skipped。

## Stage 3

- Studio 專屬環境 `python -m pytest tests/studio -q`：80 passed，2.13 秒。
- 底座環境 `python -m pytest tests/test_baseline_contract.py tests/test_decoupling.py -q`：9 passed，3.34 秒。
- Ruff check 通過。新增 Pydantic 2.14.3，Studio IR 與 Runtime 契約分離。
- 包含未知版本／欄位／強轉、缺少工具、循環／非法依賴、型別、路徑、政策、證據、
  JSON Schema 同步、版本衝突與不可變儲存 roundtrip。詳見 workflow-ir.md。
- Stage 2 commit：`6cb66ec`。本階段無 DB schema 變更；回滾前備份 Studio DB，
  舊版只可保留未知 IR 草稿，不能將其當成已驗證可發布版本。

## Stage 4

- Studio 環境 `python -m pytest tests/studio -q`：89 passed，2.34 秒。
- Ruff check 通過；測試包含四個實際確定性轉換、Registry CRUD／重開、重複版本、
  停用後 seed、契約匹配、平台、核准 hash、無測試證據拒絕，以及已核准工具仍受權限政策限制。
- Stage 3 commit：`30cfbd9`。新增 SQLite v003 工具與核准表；舊有 migration／backup／restore 回歸通過。
- 內建平台宣告表示純 Python 實作的預期相容性，目前僅 Windows 實測；三平台整合 Gate 仍未執行。

## Stage 5

- Studio 環境 `python -m pytest tests/studio -q`：108 passed，3.33 秒；Ruff check 通過。
- 固定 Provider 驗證文字／Mermaid 候選 IR、逐段事件、重開、Prompt Injection、缺工具、
  模糊分支、非法 JSON 契約、來源／目標篡改、中斷、取消、關閉迭代器、預算與明確外傳範圍。
- SSE 模擬傳輸驗證 Unicode、usage、完成標記、截斷／工具呼叫拒絕、缺金鑰與重新導向拒絕。
- 完成事件與 Revision／usage 同交易提交；帳本未取得 usage 時保留成本估計。
- Stage 4 commit：`ecf3e9a`。新增 SQLite v004 事件／用量資料表。
- 真實付費模型及真實業務推導品質未驗證；沒有傳送本機私人來源。這不是最終產品品質 Gate。

## Stage 6

- Studio 環境 `python -m pytest tests/studio -q`：116 passed，3.83 秒；Ruff check 通過。
- 包含完整成功資料夾、多候選、只有成果、同名異內容、日誌敏感內容不進圖、誤導時間順序、
  中間步驟、比對上限、候選 IR 驗證、關係確認／替換／移除、版本保留與 Golden Case 指紋／封存。
- Stage 5 commit：`aade591`。新增 SQLite v005 證據圖與 Golden Case 版本資料表。
- 不把樣本重播匹配當成已還原完整業務規則；Runtime 套件重播由 Stage 7–8 驗證。

## Stage 7

- Studio 環境 `python -m pytest tests/studio -q`：124 passed，9.18 秒；Ruff check／format 通過。
- 實際 Runtime 獨立 Python 環境執行生成複製包及文字整理→指紋報告包，經 WorkflowService
  plan／prepare／verify／run；驗證實際生命週期事件、正確 bytes、新 run_id、快取與 registered_requests=0。
- 不同輸入覆寫既有工作輸出被底座正確拒絕；測試改用新 output 後通過，未改底座保護。
- 靜態拒絕未支援重試、參數、非終端輸出、索引路徑衝突及後續停用工具；wheel 完整性／去依賴／可重現通過。
- 既有 tools/release.py.build_project 實測成功；產物位於
  `dist/studio-stage7-release/be13f0d900184608942a1cc45f15b241/release-check/release/`。
  ZIP SHA-256：`921a92929657c7ba2015e61fa157d1b9284a8eaae56dc0d7363b0d58bc2298eb`。
- Stage 6 commit：`3621b8a`；新增 SQLite v006 建置紀錄。尚未把候選包當成已驗證／已發布。

## Stage 8

- Studio 環境 `python -m pytest tests/studio -q`：131 passed，22.35 秒。
- 之後新增並執行 `tests/studio/test_verifier.py::test_self_signed_untrusted_template_still_never_executes`：1 passed，0.26 秒。
- Ruff check／format 通過。Static Gate 在任何動態執行前拒絕 tamper，以及重新自簽 hash 的未知程式模板。
- 實際隔離 Runtime 重播驗證內容相符／不符、Runtime 合約失敗、重複提交獨立 Run、
  報告保存、兩輪修復仍失敗停止、成功修復建立新版本，以及修改成功標準遭拒絕。
- Stage 7 commit：`66427a1`；新增 SQLite v007 不可變驗證報告表。
- 真實模型 proposer 未呼叫，非 Windows 平台／真人 UI 仍待；詳見 verifier.md 的能力邊界。

## Stage 9

- Studio 環境 `python -m pytest tests/studio -q`：143 passed，29.26 秒；Ruff check 通過。
- 實際 Runtime 執行條件為真／假兩路，驗證結果與 branch.json；錯誤 operator、巢狀、不同輸出、非法參數拒絕。
- Runtime LLM 使用 fake HTTPS opener（無外部呼叫），驗證逐段可見文字、最後結構、
  input／cached／output usage、未授權 preflight 與預算不足時 network calls=0。
- 缺授權的外層錯誤依底座既有契約為 PREFLIGHT_BLOCKED，內層模型 Gate 原因為 APPROVAL_REQUIRED。
- 確定性流程的原有 Runtime／Verifier 零 API 測試均繼續通過。
- Stage 8 commit：`3f0d77d`。Stage 9 無 DB migration；新條件使用受控 parameters schema，不變更既有 IR v1 欄位。
- 真實付費模型、外部業務正確性與平台 Gate 未驗；預設 Verifier 不持有 API 金鑰。

## Stage 10（P2 保持停用）

- `python -m pytest tests/studio/test_video_ingestion.py tests/studio/test_video_discovery.py -q`：8 passed，2.49 秒。
- 本機 FFmpeg／ffprobe 實際產生與解碼五秒測試影片，五個影格時間為 0–4 秒，比例維持 16:9。
- 測試預設停用、損毀拒收、影格位置驗證、未知操作、禁止 verified、串流中斷、多來源衝突、明確選取額外來源。
- Stage 9 commit：`0db50de`。模型分析使用 fixture，不宣稱真實影片理解品質通過；維持預設停用。
- 無 DB migration；影片／影格沿用來源版本，分析結果沿用草稿 Revision 與事件帳本。

## Stage 11 本機工作台候選

- 新增專案／來源／流程／案例／模型設定 UI、核准 hash 綁定、版本發布／回復／停用及明確模型試做授權。
- 實際 Windows 原生畫面檢查與使用者回饋見 ui-review.md；不將此當成 20 件非技術案例 Gate。
- 最新非桌面回歸：160 passed、1 failed、3 deselected；失敗揭露案例清單依 UUID 而非建立時間排列，已修正。
  修正後多交付物案例與 Golden Case 回歸 2 passed（4.03 秒）。三個桌面測試有先前獨立程序證據；本輪遵守使用者停止桌面操作，不重跑畫面控制。
- UI 清晰度／多行／狀態／未儲存／視窗邊界專項 2 passed；實際按鈕完整流程專項 1 passed；串流顯示專項 1 passed。
- 非機密模型設定保存於 SQLite v009，金鑰只在記憶體、依目的端區分。v008 保存核准及發布紀錄。
- 整體 P0/P1 尚未驗收，特別是缺工具時的新 Handler 生成路徑仍需補齊，詳見 todo.md 完整性審核待補。

## 生成 Handler 回修

- 工作台候選已提交 `1144f31`。補上模型產生受限 Python Handler、typed arguments、合成測試、兩輪修正、原始碼與測試產物。
- 新增 CSV 多條件篩選及 JSON 分組加總，已由真正 WorkflowService 執行；CSV 案例另經獨立 Verifier 重播。
- 非桌面完整回歸：201 passed、3 deselected，58.43 秒。沒有執行桌面控制，也沒有呼叫付費模型。
- 其後加入停用能力不對模型宣告、未宣告套件檔案拒絕，相關發現／Verifier 測試 10 passed，8.36 秒。
- 安全案例涵蓋模組／檔案／網路／反射拒絕、有界操作／配置、循環資料、格式膨脹、稀疏 CSV 及錯誤程式行號。
- 已保存可信 v1 模板，並實測前一版模板仍能被驗證、執行；未知模板仍拒絕。
- 上述程式生成的模型回應使用 fixture；真實模型品質、完整需求覆蓋、三平台及非技術案例 Gate 仍未完成。

## 驗收比較與交易回修

- Handler 回修已提交 `28c0f29`。本輪依原規格 Stage 8 步驟 4 補明確的 JSON 內容比較，預設仍逐 byte。
- `python -m pytest tests/studio/test_comparison.py tests/studio/test_case_transactions.py -q`：27 passed，4.01 秒。
  包含精確數值／型別／陣列語意、重複欄位與非法 Unicode 拒絕、大小／深度／數量上限，
  真正 WorkflowService 重播、規則版本與核准失效，以及案例／候選保存失敗完整交易回復。
- `python -m pytest tests/studio -q -k 'not test_real_tk_buttons and not test_tk_analysis and not test_ui_state_multiline' -rs`：
  230 passed、3 deselected，69.89 秒。使用 Studio `.venv/Scripts/python.exe`，未操作使用者桌面、無外部模型呼叫。
- 完整回歸後修正第二輪 RepairLoop 應接續上一輪 IR，避免丟失第一輪修正。
  `python -m pytest tests/studio/test_repair.py -q`：4 passed，13.45 秒，含累積兩輪修復的實際 Runtime 重播；
  所有輪次 acceptance_hash 相同。此專項與上項重疊，不相加為整體測試數。
- Ruff check、format check（114 files）及 `git diff --check` 通過。
- 新增比較規則選擇對話框的程式已接線，但遵守停止桌面操作，未做此對話框的視覺複驗。
- 完整規格缺口見 requirement-audit.md；本輪未完成真實模型、三平台、20 件非技術案例及最終發行 Gate。

## 業務試做模型修復接線

- 新增 `StudioService.model_repair`、不可變 PreparedRepair、逐段事件、累計設計預算與獨立外傳確認。
  原報告與完整案例清單／比較規則 hash 固定；目的端、預算、工具、案例或專案漂移均拒絕。
- `python -m pytest tests/studio/test_model_repair.py -q`：15 passed，22.84 秒。
  真正 Runtime 執行了修復後的新包，包含 CSV 篩選邊界及門檻的兩輪累積修復，案例 acceptance_hash 不變。
  Runtime LLM 憑證重新綁定及端點／預算變更拒絕使用替身 replay，不是實際雲端模型呼叫。
- 完整非桌面回歸（與前節相同排除三個桌面測試的命令）：246 passed、3 deselected，82.67 秒。
- 隨後新增缺規則的結構化問題事件與 UI 顯示；
  `python -m pytest tests/studio/test_model_repair.py -q -k missing_business`：1 passed、15 deselected，1.93 秒。
  缺規則只呼叫一次、保存待確認問題，未建立虛構成功版本。
- Ruff check／format check（116 files）及 `git diff --check` 通過。
- 工作臺按鈕與確認／串流／報告接線已實作，UI model 完整流程已驗；未啟動視窗做視覺驗收。
  全部模型輸出仍為 fixture，未呼叫付費 Provider，未發布到外部服務。

## 版本續編與案例編輯

- 新增 `project/history.py` 及工作臺版本／案例歷史視窗：版本差異、從歷史另存新版、明確還原舊目標、
  未發布草稿捨棄、案例輸入／預期／規則／交付物修改、封存後由歷史重新建立使用版本。
- 專項與原有專案回歸：
  `python -m pytest tests/studio/test_history_editing.py tests/studio/test_case_editing.py tests/studio/test_projects.py -q`：
  21 passed，3.69 秒。首次執行因新測試誤用工具欄位名稱失敗，改用既有 record_version 契約後通過。
- 完整非桌面回歸（同前節命令）：258 passed、3 deselected，94.83 秒。
  包含舊版資料保持不變、續編版號不回退、目標還原失敗交易回復、跨專案／過期編輯拒絕，
  正式 manifest 不因續編改變，以及案例更新使舊核准失效。
- 隨後將「捨棄／核准」再檢查放入同一寫入交易，防止驗證與 INSERT 間的競爭。
  `python -m pytest tests/studio/test_history_editing.py tests/studio/test_case_editing.py tests/studio/test_governance.py -q`：
  15 passed，13.62 秒，含注入捨棄發生於核准檢查中的競爭路徑；不與完整回歸加總。
- Ruff check／format check（121 files）及 git diff --check 通過。
- 新歷史視窗、唯讀狀態及捲軸尚未實機視覺複驗；遵守使用者停止桌面控制的限制。
  案例歷史的二進位內容仍需直接預覽能力，完整節點／規則／工具管理等缺口仍在需求審核表。

## 流程節點、連線與交付物編輯

- 新增 `FlowEditor` 與 GraphDraft／工作臺連線視窗；工具模板使用當下政策，整份修改同時檢查引用、
  型別、DAG、路徑、輸出與 Compiler 支援，再保存新 Revision。可同時重新連線並刪除被取代的步驟。
- `python -m pytest tests/studio/test_graph_editing.py tests/studio/test_ui_workflow.py -q -k 'not test_real_tk_buttons and not test_tk_analysis'`：
  17 passed、2 deselected，7.68 秒。首次 fixture 因 Windows write_text 換行轉換失敗，改用固定 UTF-8 bytes 後通過；未放寬驗收規則。
- 真實底座重播「文字整理 → 指紋報告」，結果與獨立預期 bytes 相符，無 Runtime 模型授權或呼叫。
  另驗證循環／懸空引用／型別不符／路徑穿越／非終端交付／重複節點拒絕、重用生成 Handler、
  過期編輯／停用工具阻擋，以及捨棄較新版本後續編仍使用遞增版號。
- 完整非桌面回歸（同前節排除三個桌面測試命令）：272 passed、3 deselected，95.06 秒。
- Ruff check／format check（124 files）與 git diff --check 通過。新視窗來源選項以步驟名稱顯示，
  未進行實機視覺操作；這些程式檢查不能代替操作驗收。
- 完整條件／模型政策表單、手動空白流程起案、規則與工具治理等仍待；詳見 requirement-audit.md。

## 規則證據確認、撤回與 v010

- 新增 append-only 人工決策與內容指紋核對，接入保存／編輯／續編／編譯／靜態驗證／模型修復。
  工作臺可修改規則說明及引用步驟、確認／撤回、查看理由與來源；不能藉此修改算法或授權外部工具。
- `python -m pytest tests/studio/test_evidence_decisions.py tests/studio/test_migrations.py tests/studio/test_governance.py -q`：
  28 passed，18.35 秒。包含來源／版本／claim／locator／ID 變更、跨專案與機器標記冒用拒絕，
  交易失敗回復、真正包重播及模型修復保留確認、撤回期間發布不切換舊 manifest。
- v009 → v010 升級前備份、原專案保留、SHA 校驗與還原到獨立 v009 DB 通過。
  決策使用持久化 sequence；VACUUM 後相同內容的舊確認仍失效，新確認可重新成立。
- 最終非桌面完整回歸（同前節命令）：288 passed、3 deselected，107.58 秒。
- Ruff check／format check（128 files）及 git diff --check 通過。
- 新共用來源預覽元件支援文字與圖片等比顯示，案例歷史已改讀精確版本的 bytes。
  PNG 舊／新版本內容與尺寸資料測試通過；沒有操控使用者桌面，原生渲染／排版未驗。
- 這些是本機功能與 fixture 證據，不代表真實模型品質、機器 verified 證據整合、三平台、
  20 件非技術案例或完整交付已完成。剩餘工作見 requirement-audit.md。

## 手動起案、分支與步驟設定

- 新增不經模型的第一個流程建立入口；來源、目標、工具、格式及輸出一次驗證後保存，失敗不留下半份 IR。
  證據維持 inferred，沒有宣稱自動理解或已驗收。
- 步驟表單接上檔名／時間／快取／前置及完成檢查、typed arguments、兩路分支、模型參數及結構化輸出欄位。
  新模型步驟使用 symbolic secret；編輯時檢查整份流程的模型預算總額。
- 分支驗證依兩路可信工具的共同輸出契約核對格式，保留原 generic file 宣告的相容性，
  也允許已知共同文字格式接到下游文字工具，不偽造格式轉換。
- `test_manual_and_settings.py test_branches.py test_graph_editing.py`：35 passed，11.53 秒。
  後續增加新分支／模型模板與 secret 宣告測試，`test_manual_and_settings.py`：17 passed，5.60 秒。
- 完整非桌面回歸（同前節命令）：305 passed、3 deselected，109.35 秒。
  包含手動流程真實重播、分支兩路真實輸出與 branch.json、型別／來源／路徑／重複初始化拒絕、
  模型政策變更令舊授權失效、預算總額與外部快取限制，以及非有限數值與微美元上限取整。
- Ruff check／format check（131 files）與 git diff --check 通過。後續表單預設值、條件選取及提示微調未操作桌面，
  新表單的實際互動與渲染仍須實機驗收；未呼叫付費模型或對外發布。

## 工具治理與 v011

- 新增工具描述／版本管理 UI、雜湊＋record_version 審閱、核准撤回及不可變工具事件。
  註冊預設 untrusted；available 與 allowed 分離，缺少受控適配或低報必要權限者不提供模型使用。
- `test_tool_management.py test_registry.py test_matching.py test_tool_policy.py`：22 passed，4.21 秒。
  自訂已核准複製工具真正完成重播與發布，停用及撤回阻擋後續操作而保留舊 manifest。
  另驗證版本不變、過期審閱、JSON 自授權拒絕、事件失敗交易回復及 v010 備份／v011 升級／獨立還原。
- 完整非桌面回歸（同前節命令）：319 passed、3 deselected，110.51 秒。
- 之後為 Discovery 補上能力、資源、權限與成本宣告，同時排除本機測試路徑；
  `test_tool_management.py test_discovery.py test_handler_discovery.py`：30 passed，10.13 秒。
  修復 Provider 亦不收到 test_evidence／implementation 路徑，不將內部稽核資料當模型輸入。
- Ruff check／format check（133 files）與 git diff --check 通過。新工具視窗未做實機驗收，
  生成 Handler 的跨專案重用、第三方適配、真實模型與最終平台／使用者 Gate 仍待完成。

## 固定 Handler 跨專案工具庫與 v012

- 新增從目前已通過獨立試做的步驟保存固定 Handler；程式、參數型別、預設值與合成測試的 hash
  綁入工具契約，預設 untrusted。另存版本保留舊內容，只有參數值可在新專案覆寫。
- `test_handler_library.py test_model_repair.py`：28 passed，41.29 秒，含新專案不同門檻真實重播、
  原專案封存後重用、固定內容不可覆寫、舊 build 不能冒充新步驟驗收，以及修復只傳介面與覆寫參數。
- 完整非桌面回歸（同前節命令）：332 passed、3 deselected，130.45 秒。
- 隨後補保存／展開大小限制、來源紀錄版本與契約入庫檢查，最終 `test_handler_library.py`：
  14 passed，25.76 秒。大小限制故障注入確認不留下部分工具或目標 Revision；不與完整回歸加總。
- v011 → v012 及 before-v011 在獨立目錄還原通過；模板表 UPDATE／DELETE 拒絕。
  ToolDescription schema／既有 fingerprint 不變，舊工具不自動升格為固定 Handler。
- 模型請求不含固定程式、合成測試及預設參數；比較差異仍依修復授權範圍傳送。
  程式與合成測試的私密 sentinel 沒有出現在 fixture Provider 請求中，實際執行仍使用完整本機快照。
- Ruff check／format check（136 files）及 git diff --check 通過；新保存按鈕與表單未做實機操作。
  真實模型、第三方覆蓋、完整治理、三平台、20 件非技術案例及最終交付仍未完成。

## 建置審核、完整案例 Gate 與 v013

- 新增建置／報告／文字檔預覽／保存事件介面，送審、退回、撤回核准及未發布建置封存／恢復。
  審核與技術結果分離；同一 Revision 的重新建置不能繞過退回，新報告以 review_sequence 證明在退回後開始。
- 補齊全部有效案例集合、目前來源版本及指定交付物檢查；新增案例或更新來源後不能沿用舊驗收。
  案例 UI 可先預覽目前來源，再明確保存新版參照，原版本不被改寫。
- 新增發布停用入口及操作者／理由；相同 manifest 不能僅靠 hash 推斷新核准已發布。
  相同報告與決策的重複核准保持冪等，新報告形成新的核准與發布關聯。
- ZIP 使用固定時間及模式，實測不同檔案 mtime 產生相同 package_hash；未宣稱此測試代表三平台位元組完全一致。
- `test_build_reviews.py test_governance.py`：18 passed，31.95 秒。含退回、同修訂重建、
  封存、過期審閱、交易失敗、相同 manifest 發布中退回、停用保留成果、指標／核准關聯及歷史回復繞過拒絕。
- 最終非桌面完整回歸（同前節命令）：349 passed、3 deselected，160.13 秒。
- v012 → v013 與獨立 v012 備份還原通過；審核 UPDATE／DELETE 拒絕。
  Ruff check／format check（139 files）及 git diff --check 通過。
- 已開始的試做不因封存自動取消；有 prepared 發布紀錄的建置暫時受到保護，核對／清理 UI 仍待。
  新視窗、來源更新勾選及停用操作未實機驗收；真實模型、完整故障／平台矩陣與最終交付仍未完成。

## 發布準備核對、鎖保護與 v014

- 新增發布歷史的準備核對入口，區分已生效、未生效及無法判定；保留套件、暫存檔與成果。
  結束未生效準備後可撤回核准並封存建置；後續發布只接受有完整快照依據的已知殘留。
- `test_release_recovery.py` 首批 14 passed，27.38 秒；加入停用中斷、後續準備／發布取代
  舊紀錄及原工作區遺失後 19 passed，35.49 秒。之後再補 UI model 重開及核對持久化。
- 非桌面完整回歸：369 passed、3 deselected，195.22 秒。命令：

  ```powershell
  & routinekeel/workflow_studio/.venv/Scripts/python.exe -m pytest tests/studio -q -k 'not test_real_tk_buttons and not test_tk_analysis and not test_ui_state_multiline' -rs
  ```

- 完整回歸啟動後補上目前版本讀取時的發布鎖及相應競爭測試；最終程式另執行
  `test_release_recovery.py test_governance.py test_build_reviews.py`：38 passed，70.28 秒。
  此結果與前項分開記錄，不將執行中的舊模組測試當成最終鎖修正的證據。
- 涵蓋 journal／wheel／manifest／指標中斷、部分暫存檔、相同 manifest 的舊決策保留、
  遺失前一指標、鎖占用／過期觀察、wheel 損毀、來源工作區／專案歸屬、稽核失敗回復、
  未知檔案保護、撤回核准不恢復、停用中斷保留成果及被後續準備取代。
- v013 → v014、before-v013 備份校驗及獨立還原通過；核對紀錄不可刪除，UI model 重開可見結果。
  Ruff check、format check（142 files）與 git diff --check 通過。
- 未重新操作桌面或外部發布；三個桌面測試排除。新核對視窗未視覺／互動驗收，
  這組故障測試不取代原規格完整 20 故障矩陣、三平台、真實模型與非技術使用者 Gate。

## 資料夾關係、角色與版本決策

- 新增關係檢查視窗：角色草稿／重新比對、確認／改為待確認／排除／同端點候選替換、
  歷史版本、固定來源文字／圖片預覽、決策理由及未知事項。新視窗未操作桌面驗收。
- 採用前重新比對圖版本、來源、工具與目標，拒絕過期或修改過的記憶體候選。
  有理由的人工決策綁定固定來源、路徑、工具、參數與圖版本，與 IR／Golden Case 同交易保存。
  舊紀錄沒有理由時不自動升格，機器 verified 證據仍須另行實作。
- `test_folder_review.py test_folder_discovery.py test_evidence_decisions.py` 初次 33 passed，13.08 秒。
  包含採用後真實底座試做、確認紀錄可查、來源替換仍可預覽舊版、關係變更令舊候選失效、
  角色重比對清除確認、替換歷史、CAS 拒絕、封存保護及案例交易失敗整體回復。
- 非桌面完整回歸（同前節命令）：381 passed、3 deselected，201.95 秒。
  此輪啟動後另外修正候選生成的版本編號須包含已捨棄修訂；最終上述三檔專項
  36 passed，13.56 秒，直接驗證不重用編號、舊修訂不變、排除最後關係標記未知及舊決策不冒充確認。
- Ruff check、format check（144 files）及 git diff --check 通過。沒有 schema 遷移，沿用 v014，
  graph payload 新增可選 decisions；回退先保存最新 DB／projects，來源與舊圖不覆寫。
- 新增表單提示／未保存角色提示等 UI 修改僅經靜態檢查，不能以非桌面測試宣稱互動或視覺通過。
  真實模型、機器證據、手繪辨識、完整故障／三平台／非技術案例與最終交付仍未完成。

## 固定 Runtime 試做的機器驗證證據

- 新增從通過報告加入 verified 證據的服務與工作臺入口；陳述固定案例、比較方式、執行 ID 及指紋，
  不把人工確認或任意業務規則改標為機器驗證。保存／編譯／靜態驗證／修復共用查核。
- 查核原 ZIP、Runtime 套件副本、固定案例來源、保存報告、執行 ID／生命週期、實際輸入及交付產物，
  並重新執行比較規則。核對只讀檔，不重跑流程、不呼叫模型、不授予發布權限。
- 修改處理內容會在流程編輯及修復時降級舊機器證據；原始 IR 直接帶過期 verified 狀態會被拒絕。
  規則說明修改不改執行依據，歷史版本與報告保留。新版本仍需重新建置及驗收。
- 首批 `test_machine_evidence.py test_verifier.py test_model_repair.py test_evidence_decisions.py`：
  53 passed，63.98 秒。後續補 JSON 比較、兩輪修復狀態及 UI model 重開後，機器證據專項
  18 passed，29.50 秒；不與前項加總。
- 最終非桌面完整回歸（同前節命令）：400 passed、3 deselected，230.30 秒。
  包含真實重播再建置、偽造結論／來源／標籤／執行內容拒絕、產物及套件副本損毀、
  失敗／其他專案報告拒絕、版本衝突、事件交易回復、JSON 相同但 bytes 不同、修復兩輪降級及重開。
- 交付 metadata 的 primary 路徑在讀取前做邊界與重新解析點檢查，越界測試通過。
  Ruff check、format check（146 files）、git diff --check 通過。
- 無 schema 遷移，沿用 v014 的不可變報告／案例／Revision。機器引用依賴保存的原套件及 verification
  產物，備份／回退方式見 machine-evidence.md。三個桌面測試仍排除，新增入口未實機驗收。
  真實模型、手繪圖片分析、完整故障／平台矩陣、20 件非技術案例及最終交付仍待。

## 靜態圖片 Discovery 與範圍預覽

- 一般分析接上 PNG／JPEG／WebP，可與文字／Mermaid 混合；圖片完整解析度校正方向、移除額外 metadata，
  原來源不變。圖片區域引用驗證、模糊問題／矛盾保存待確認草稿，不能直接編譯。
- 新增固定傳送範圍預覽、圖片用量預留設定與設計期帳本估算；每輪修正保留原圖片。
  圖片預留不是供應商計價保證，需依服務調整。準備來源改在背景進行，確認前無傳送。
- 原文字／Mermaid、SSE、UI model 回歸：23 passed、2 deselected，6.53 秒。
  圖片／Discovery／SSE 專項 40 passed，4.84 秒；之後補高位深灰階，圖片專項 22 passed，3.63 秒。
- 最終非桌面完整回歸（同前節命令）：422 passed、3 deselected，227.88 秒。
  包含三種格式、完整像素／EXIF 方向／metadata 去除、22 個圖片情境、圖文混合、未選來源不外傳、
  frozen request 拒絕竄改、實際 SSE body、用量設定保存、預算阻擋、斷線與同範圍修正，
  fixture 產生的圖片候選實際編譯後交由底座重播通過。
- 動畫／多格圖片明確拒絕，不只送第一格；影片來源不能繞過 P2 隔離。高位深灰階值 1,000／50,000
  在 metadata 移除後保留，未因轉 RGB 被截斷；這不等於真實模型理解了圖中的內容。
- Ruff check、format check（149 files）、git diff --check 通過。沿用 v014，新增可選設定，
  備份／回退限制見 image-discovery.md。最後預覽尺寸文字調整僅靜態檢查，未操作桌面。
- 真實辨識品質、所有新增表單實機、平台矩陣、非技術使用者及最終交付仍待。
  另從程式核對發現背景工作完成時 refresh_project 會覆蓋未保存目標欄位，已列入需求審核接續修正。
