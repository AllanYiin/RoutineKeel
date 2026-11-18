# 原規格對照與未完成工作

依 `specs/requirements.txt` 第一部分及 Stage 0–13 核對。此文件是工作清單，並非完成聲明。
本機已驗證的程式能力不等於真實模型品質、非技術使用者或三平台驗收。
原始需求不因實作目前較窄而縮減；目前 Stage 12 為主要驗收階段，測試揭露的功能缺陷仍須回修。

## 功能契約

| 原需求 | 現有證據入口 | 仍需完成或補強的證據 |
|---|---|---|
| FR-01 專案 CRUD／續編 | project/service.py；test_projects.py、test_ui_workflow.py | 確認 UI 軟刪除、第三版草稿續編及正式歷史保留的完整使用者路徑 |
| FR-02 來源 CRUD／預覽 | ingestion/service.py、preview 與 folder 模組；來源相關測試 | UI 說明修改；所有已接受格式的最終視覺矩陣 |
| FR-03 草稿版本與差異 | project/history.py、版本與差異 UI、test_history_editing.py | 續編新版本／明確還原目標／捨棄保護已驗；新視窗實機走查待驗 |
| FR-04 業務規則 CRUD | governance/evidence.py、machine_evidence.py、規則／步驟／資料夾關係表單；test_evidence_decisions.py、test_machine_evidence.py、test_folder_review.py | 人工與固定試做機器證據已分開綁定，修改處理內容使舊機器狀態失效；完整實機走查仍待 |
| FR-05 工具註冊及政策 | registry/catalog.py、handlers.py、policy.py、available、v011–v012、工具管理 UI | 描述管理與同工作區 Handler 跨專案重用已實測；第三方適配覆蓋及新視窗實機驗收仍待，模板庫目前以同工作區為範圍 |
| FR-06 節點 CRUD | ir/editor.py 原子編輯；GraphDraft／手動起案／步驟設定表單；test_graph_editing.py、test_manual_and_settings.py | 手動起案、基本動作／分支／模型新增、來源／依賴／交付物／條件／模型政策已補；新增表單實機與完整工具型別覆蓋仍待驗 |
| FR-07 案例 CRUD／預期實際 | GoldenCases.save/get/archive/history；案例編輯、文字與圖片歷史預覽；test_case_editing.py | 版本化修改、替換、封存後重開及核准失效已驗；新圖片預覽與視窗走查待驗 |
| FR-08 生成版本 CRUD | Compiler、BuildReviews、ReleaseRecovery、建置／報告／發布核對 UI | prepared 核對、保留檔案結束準備與再封存已實測；新視窗實機驗收仍待 |
| FR-09 人工決策 CRUD | Approvals、v013 審核紀錄與 sequence、送審／退回／撤回 UI | 技術結果與審核分離、同修訂退回約束及重送已補；完整操作走查與故障矩陣仍待 |
| FR-10 發布／回滾／停用 | governance/release.py、recovery.py；test_governance.py、test_release_recovery.py、fault-matrix.md | Windows 20 個固定發布故障點及三個真正程序中止已驗；跨專案相同 workflow_id 的直接回歸與跨平台仍待 |
| FR-11 明確工作目標 | project/models.py 與 IR goal 驗證 | 對照非技術完整流程確認輸入、預期與成功條件，不把簡短文字等同完整需求 |
| FR-12 多來源發現 | discovery/planner.py、images.py、folder.py、video.py；test_image_discovery.py 與推論測試 | 手繪／文字混合來源、固定圖片傳送與待確認路徑已接線；真實文字／Mermaid 各至少 5 件及手繪辨識品質仍待；影片與融合仍為 P2 未驗且停用 |
| FR-13 工具配置 | Registry policy/matching、受控內建及生成工具 | 對真實案例核對重用優先順序，不以僅固定工具通過代替新 Handler 能力 |
| FR-14 程式／包生成 | compiler/handlers.py、runtime_program.py、generator.py；test_generated_handlers.py | 已有 CSV／JSON 新算法重播，真實模型未驗；單一檔案 source/result 及受限語法需對照完整案例覆蓋，不可默認為全範圍完成 |
| FR-15 Runtime LLM 隔離 | compiler/runtime_llm.py、LLM ledger、authorization.py、ModelRepair／UI 接線 | 修復已具獨立授權與固定案例重播；真實 Provider 品質／用量及成本 gate 未驗 |

## 共通契約與驗收

| 原需求 | 現有證據 | 未完成驗收 |
|---|---|---|
| §8 IR、邏輯參照、節點強制契約、有限分支 | ir 模組、compiler/compatibility.py、branch_lowering.py；IR／branch 測試 | 逐欄位核對 Runtime 語意與拒絕路徑；不能把 IR 接受但 Compiler 不支援當成已實作 |
| §9 狀態／migration／不可變／交易 | SQLite v014、遷移備份、CAS、不可變紀錄、100 次專案循環及 20 發布故障／程序中止 | 其他長操作取消與完整跨平台狀態矩陣仍待 |
| §10 共用 Service／事件／串流 | service.py、discovery events 與 usage；operation-events.md | 已補生成階段、驗證完成與待核准事件及交易回滾測試；CLI 目前只有部分入口，全部狀態與硬中止仍須逐項核對 |
| §11 UI／預覽／停止／顏色＋文字 | 原生 Windows 檢視與使用者回饋；ui-review.md、project-drafts.md、test_project_edits.py | 背景重讀保留草稿、版本衝突與比較更新已補控制器測試；不操作目前使用者桌面，其他平台及新增表單仍待實機走查 |
| §12 非功能與基準 | acceptance-benchmarks.md／fault-matrix.md：五項基準已有指定 Windows 環境證據，包含 20 固定故障 | 其他平台仍待；不推論所有硬體或實體斷電情境皆通過 |
| §13 Error／Abuse cases | 各模組拒絕測試及 error codes | 逐情境對照報告，確認取消／斷線／外部結果不明不重試；禁止刪掉失敗測試 |
| AC-01 文字三步生成 | fixture discovery、真實 Runtime replay | 真實模型完整文字三步；至少五件文字及五件 Mermaid |
| AC-02 第三版續編 | test_acceptance_lifecycle：第三版來源／證據重開、續編第四版重播、舊發布包不變 | 本機工程路徑已驗；原生視窗走查仍待 |
| AC-03 中斷 | 固定串流斷線、failed 事件、控制器訊息及上一候選重開 | fixture／本機工程路徑已驗；真實 Provider 與原生視窗仍待 |
| AC-04 未授權寄送 | test_security_end_to_end：外寄描述／自授權來源被拒、不能建置核准發布、SMTP/socket/HTTP 呼叫 0 | 目前未支援真實寄送適配，未宣稱其已授權路徑完成；負例工程驗收已具證據 |
| AC-05 確定性零 LLM | 已核准發布至隔離工作區的 copy 包，連續 100 次不同輸入均 registered_requests=0，事後重核全部交付 | 本機固定案例已驗；仍需完整範例與平台矩陣，不推論任意流程皆正確 |
| AC-06 成功資料夾 | acceptance-v1 C11–C15 從資料夾推導、訓練與未見資料真實重播、隔離發布；C15 含中間產物 | Windows 工程路徑已驗；原生操作與真人結果另列，未用工程測試替代真人 Gate |
| AC-07 平台報告 | policy 平台拒絕與 package compatibility；fb1e864的run44247339245 三平台各529通過，同一IR與輸出hash一致 | 目前程式工程矩陣已有證據；不含原生GUI、實際模型或未來發行包的安裝驗收 |
| AC-08 五步影片 | FFmpeg 真實影格、fixture 推導 | 真實理解品質未驗，P2 維持停用 |

## 最終交付 Gate

- acceptance-v1 已準備 20 件低風險範例、21 組訓練與 40 組未見資料，見 acceptance-corpus.md；
  資料完整性／參考答案通過不算轉換成功。真實模型與至少 16 件非技術使用者完成仍未有證據，結果模板全部 not_run。
- Stage 8 已有明確 JSON 比較、最多兩輪 ModelRepair Provider／UI 接線及獨立範圍／預算授權，
  CSV 算法及參數修復已用真正 Runtime 重播；模型輸出目前仍為 fixture，真實品質及新按鈕視覺驗收待完成。
- Stage 12：Studio 三平台 CI 與獨立 Runtime／Studio 環境的 run44245189603、最新事件修正的
  run44247339245 均成功，artifact已核對。詳見 platform-acceptance.md；完整真人/模型Gate仍未完成。
- Stage 13：入門／開發／隱私／限制／升級回滾文件與入門案例已備，案例由 UI model 與真正 Runtime 重播；
  已新增候選 ZIP／啟動器、逐檔清單／hash、私密路徑與追蹤來源檢查，以及 Windows 非桌面整包安裝；
  尚需收齊三平台安裝、原生視窗／真人與最終 commit 證據，詳見 distribution.md。
  開發用 open_studio.bat 不等於乾淨安裝交付，文件存在與工程重播不代替真人走查。
- 目前工作樹底座／流程回歸 383 passed、21 skipped、1 桌面測試 deselected，沒有失敗；
  使用者既有變更保留，後續最終變更仍須對照有效範圍，不把工作樹結果冒充乾淨 release。
- 最終審核須再展開每階段的命名產物與測試要求；本表只提供主索引，不能直接拿來宣告完成。
- Stage 7 原文明列「不支援 IR 正確阻擋」及不支援功能／原因輸出，不能另把所有任意型別與工具適配都推定為必要 Gate。
  同時也不能用拒絕路徑替代明定的 P0/P1 功能；須以原始案例與契約逐項驗證。兩個固定 Golden Case
  的直接 Runtime 測試已位於 test_runtime_compatibility.py，完整核心 DoD 仍需連同其他命名產物核對。
