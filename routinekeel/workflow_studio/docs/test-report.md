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
