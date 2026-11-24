# 底座通用步驟與 Documa 實測紀錄

執行範圍為[分階段開發計畫](implementation-plan.md) Stage 0–6。證據對照[技術規格](technical.md) RK-BS-01 至 RK-BS-07。舊有藍圖補強的驗收另見[藍圖補強規格](../blueprint-completion.md)。

## Stage 0 基線

以 Windows 工作樹 `81fbe7e6775924774752caa0dd39f5a21daf1fe0` 及既有未提交差異為起點。底座、Studio 的環境均為 CPython 3.12.11，本機 Documa 為 0.9.0。底座、Studio 及共享契約沿用各自現有建置和鎖檔。底座持久資料沿用 SQLite 及工作區內的素材、交付版本和狀態目錄，Studio 保留獨立專案資料。宿主用 `ROUTINEKEEL_DOCUMA` 和 `ROUTINEKEEL_DOCUMA_SKILL_STORE` 提供本機配置，測試使用臨時工作區與固定回執。

```powershell
& flow_base/.venv/Scripts/python.exe -m pytest tests/test_graph_contracts.py tests/test_studio_design_contract.py tests/test_blueprint_install.py -q --tb=short --junitxml=dist/base-steps-devloop-stage0.xml
```

29 項通過，覆蓋既有 Graph 契約、Studio 確認、v1 藍圖候選與正式安裝入口。測試輸出留於 `dist/base-steps-devloop-stage0.xml`。本階段只補證據與進度，無執行資料要回退。

## Stage 1 定稿至正式交付

72 項通過，包含 Studio 指紋確認、Graph 候選驗收、藍圖補強與原子交付。新增具名檔案的 ZIP 與暫緩交付重跑測例。暫存副本在組裝前清除前次已知且未被人工改動的具名素材與產物索引，避免將前次檔案列入本次預覽。回退限於此組裝差異；原正式版本沿用底座交付復原流程。

```powershell
& flow_base/.venv/Scripts/python.exe -m pytest tests/test_studio_design_contract.py tests/test_atomic_delivery.py tests/test_graph_builder.py tests/test_blueprint_completion.py tests/test_base_steps.py::test_named_file_delivery_and_withheld_preview_keep_previous_version_separate -q --tb=short --junitxml=dist/base-steps-devloop-stage1.xml
```

## Stage 2 勾稽與計畫重用

5 項通過。安裝後的實際執行核對同指紋重用、當次資料差異與規則調整後重新建置。固定資料補驗計數、精確十進位容差、重鍵、缺欄與計畫指紋失效。沒有更動計畫格式或資料，回退限於本階段驗收測例。

```powershell
& flow_base/.venv/Scripts/python.exe -m pytest tests/test_base_steps.py -k reconciliation -q --tb=short --junitxml=dist/base-steps-devloop-stage2.xml
```

## Stage 3 Documa 文件與技能

4 項通過，使用實際 Documa 0.9.0 查詢 PDF、DOCX 與技能依賴。觀察實際解析器呼叫次數，驗證兩份原始文件各解析一次，重送未變更資料不再解析，更換 DOCX 時只有該份文件重建。缺少 Documa 時，無文件的圖可正常完成，有文件的圖回報 `DOCUMA_UNAVAILABLE`。v1 藍圖附帶技能的依賴也經暫存 Documa 目錄實測。其他格式的品質及不同解析器版本另依其環境驗收。本階段未改動文件或索引格式，回退限於本輪測例。

```powershell
& flow_base/.venv/Scripts/python.exe -m pytest tests/test_base_steps.py -k documa -q --tb=short --junitxml=dist/base-steps-devloop-stage3.xml
```

## Stage 4 模型審查與有限回修

30 項通過，涵蓋批評通過時轉送原候選、未通過時回修、錯誤輸出埠、缺必要審查欄位、錯誤修訂型別、截斷串流、用量上限、真人拒絕、取消與回執待核對。修正完整但格式錯誤的模型回執，使節點保存回執識別後進入失敗，依賴節點不執行。測試以已綁定的測試回執驗證宿主流程。正式模型品質依實際綁定與授權另行驗收。回退限於本輪的回執錯誤收尾，既有授權及持久化資料格式維持相容。

```powershell
& flow_base/.venv/Scripts/python.exe -m pytest tests/test_base_steps.py::test_model_critic_revise_and_judge_use_host_receipts tests/test_graph_review_external.py tests/test_graph_iterations.py -q --tb=short --junitxml=dist/base-steps-devloop-stage4.xml
```

## Stage 5 最終快照、隔離安裝與回歸

快照 `6cef7f465e263025b82e6ac4875d59e269f63f34` 的程式與測試檔 673 份已比對主工作樹位元內容。首輪底座掃出 `DOCUMENT_QUERY`、`DOCUMA_SKILL_LOAD` 未登錄，已補齊 Documa 與模型的中文診斷。首輪 Studio 有一項機器證據前置驗證失敗，該項在單獨重跑及最終 Studio 分組回歸通過。最終驗收依固定快照整組重跑結果。

| 驗收 | 實測結果 | 證據 |
| --- | --- | --- |
| Windows Studio | 566 項全通過，含 100 次具名輸出隔離執行 | `dist/base-steps-devloop-final-studio.json` 及三組 JUnit |
| Windows 底座 | 620 項通過；23 個平台限定案例以相同識別碼在 Linux 實跑通過 | `dist/base-steps-devloop-final-runtime.json`、`dist/base-steps-devloop-final-posix.xml` |
| Linux／POSIX 補測 | 59 項通過，含前述 23 項與額外的具名檔案交付、勾稽和符號連結驗證 | `dist/base-steps-devloop-final-posix.xml` |
| 既有工作流及跨宿主 | 既有工作流 105 項通過。另以安裝的 Data Adapter 重跑 MCP 到本機資料能力及升級流程 | `dist/base-steps-devloop-final-workflows.xml`、`dist/base-steps-devloop-final-portability.xml` |
| 封包與實際安裝 | 五個候選 ZIP 雜湊正確，三套件 wheel RECORD 與快照位元一致。兩個隔離 wheel 完成定稿、匯出、候選試做、正式安裝、勾稽及 ZIP 交付 | `dist/base-steps-devloop-final-integrity.json`、`dist/base-steps-devloop-final-audit.json` 及其中的 smoke 收據 |

依執行測例識別去重後為 1,314 項。兩套隔離 wheel 的實跑為 `run_615f689ee4fe496f8d6f44712767c73b`。最終本機候選位於 `dist/base-steps-final-snapshot-ib7yhhrs/dist/candidate-base-steps-devloop-final/release/`；完整工作區 ZIP 的 SHA-256 為 `aa8b2b1a6549ee5bcbf66b91bfcf24eb7b06fb1c622c86ac52c960224627da3a`。

本階段 Windows 的封裝驗證使用兩個獨立 wheel 環境。Windows 原生啟動器完整冷啟動、其他文件格式的品質走查與正式模型品質驗收，依各自的環境與授權處理。本機候選不影響目前安裝。回退可保留本輪候選和收據，選回上一份已驗證候選或既有工作流交付版本。

## Stage 6 文件與交接

規格索引、技術與白話規格、七階段指令及兩張 SVG 的本機連結與結構均已核對。白話用語檢查及 `git diff --check` 通過。另以最終候選的兩個隔離環境執行 Studio `guide` 與 Flow Base `catalog`，確認可取得宿主指南及勾稽工作流。文件回退可還原本輪的規格索引、Stage 清單與說明差異，並保留既有封包和驗收歷史。
