# 原規格對照與未完成工作

依 `specs/requirements.txt` 第一部分及 Stage 0–13 核對。此文件是工作清單，並非完成聲明。
本機已驗證的程式能力不等於真實模型品質、非技術使用者或三平台驗收。
原始需求不因實作目前較窄而縮減；Stage 7 為主要回修階段，其餘缺口依相依順序補齊。

## 功能契約

| 原需求 | 現有證據入口 | 仍需完成或補強的證據 |
|---|---|---|
| FR-01 專案 CRUD／續編 | project/service.py；test_projects.py、test_ui_workflow.py | 確認 UI 軟刪除、第三版草稿續編及正式歷史保留的完整使用者路徑 |
| FR-02 來源 CRUD／預覽 | ingestion/service.py、preview 與 folder 模組；來源相關測試 | UI 說明修改；所有已接受格式的最終視覺矩陣 |
| FR-03 草稿版本與差異 | project/history.py、版本與差異 UI、test_history_editing.py | 續編新版本／明確還原目標／捨棄保護已驗；新視窗實機走查待驗 |
| FR-04 業務規則 CRUD | discovery/evidence_graph.py 保存確認／替換／移除 | 工作臺不能只顯示 JSON；需讓非技術使用者修改規則與確認證據 |
| FR-05 工具註冊及政策 | registry/catalog.py、policy.py；test_registry.py | 管理者操作入口、生成工具可重用流程與第三方相容邊界的端到端證據 |
| FR-06 節點 CRUD | ir/editor.py 原子編輯；GraphDraft／連線交付物視窗；test_graph_editing.py | 新增基本動作、重用生成步驟、來源／依賴／輸入格式／交付物及合併刪除已補；完整條件／模型政策表單、新空白流程手動起案及實機走查仍待 |
| FR-07 案例 CRUD／預期實際 | GoldenCases.save/get/archive/history；案例編輯及歷史 UI、test_case_editing.py | 版本化修改、替換、封存後重開及核准失效已驗；二進位案例歷史的直接內容預覽與新視窗走查待補 |
| FR-08 生成版本 CRUD | Compiler.compile/get；獨立 build/report | 尚缺完整版本封存與 UI 程式／報告瀏覽 |
| FR-09 人工決策 CRUD | Approvals.approve/get/revoke 與 hash 綁定 | 待辦、退回理由及封存決策的完整操作流程 |
| FR-10 發布／回滾／停用 | governance/release.py；test_governance.py | 20 個受控故障點、跨專案相同 workflow_id 的直接回歸、跨平台實測 |
| FR-11 明確工作目標 | project/models.py 與 IR goal 驗證 | 對照非技術完整流程確認輸入、預期與成功條件，不把簡短文字等同完整需求 |
| FR-12 多來源發現 | discovery/planner.py、folder.py、video.py；推論及證據測試 | 真實文字／Mermaid 各至少 5 件；手繪辨識路徑須核對；影片與融合仍為 P2 未驗且停用 |
| FR-13 工具配置 | Registry policy/matching、受控內建及生成工具 | 對真實案例核對重用優先順序，不以僅固定工具通過代替新 Handler 能力 |
| FR-14 程式／包生成 | compiler/handlers.py、runtime_program.py、generator.py；test_generated_handlers.py | 已有 CSV／JSON 新算法重播，真實模型未驗；單一檔案 source/result 及受限語法需對照完整案例覆蓋，不可默認為全範圍完成 |
| FR-15 Runtime LLM 隔離 | compiler/runtime_llm.py、LLM ledger、authorization.py、ModelRepair／UI 接線 | 修復已具獨立授權與固定案例重播；真實 Provider 品質／用量及成本 gate 未驗 |

## 共通契約與驗收

| 原需求 | 現有證據 | 未完成驗收 |
|---|---|---|
| §8 IR、邏輯參照、節點強制契約、有限分支 | ir 模組、compiler/compatibility.py、branch_lowering.py；IR／branch 測試 | 逐欄位核對 Runtime 語意與拒絕路徑；不能把 IR 接受但 Compiler 不支援當成已實作 |
| §9 狀態／migration／不可變／交易 | SQLite v009、備份還原、CAS、版本記錄；本輪複合交易回修 | 長操作中斷狀態、100 次 CRUD 重開與正式指標故障矩陣 |
| §10 共用 Service／事件／串流 | service.py、discovery events 與 usage | generation、verification、approval 事件與所有狀態持久化須逐項核對；CLI 目前只有部分入口 |
| §11 UI／預覽／停止／顏色＋文字 | 原生 Windows 檢視與使用者回饋；ui-review.md | 不操作目前使用者桌面；其他平台實機及所有新增表單待走查 |
| §12 非功能與基準 | 安全路徑、來源 snapshots、隔離重播、無網路默認 | 100 來源零模型、20 節點 p95 <500ms、100 次 CRUD、100 次不同輸入隔離、20 故障；均需固定硬體與資料證據 |
| §13 Error／Abuse cases | 各模組拒絕測試及 error codes | 逐情境對照報告，確認取消／斷線／外部結果不明不重試；禁止刪掉失敗測試 |
| AC-01 文字三步生成 | fixture discovery、真實 Runtime replay | 真實模型完整文字三步；至少五件文字及五件 Mermaid |
| AC-02 第三版續編 | 持久化及 UI model 重開測試 | 固定第三版草稿／證據／原發布包不可變的直接整合案例 |
| AC-03 中斷 | 串流故障／草稿保存測試 | 真實 Provider、重新開啟與 UI 狀態對照 |
| AC-04 未授權寄送 | 工具政策與編譯拒絕測試 | 固定外部寄送負例的完整發布流程，外部呼叫必須為零 |
| AC-05 確定性零 LLM | 真實 WorkflowService 與 registered_requests=0 | 納入已發布包的固定 100 次隔離測試 |
| AC-06 成功資料夾 | 證據候選、GoldenCase、未知標註測試 | 使用含中間產物的固定資料集端到端驗收 |
| AC-07 平台報告 | policy 平台拒絕與 package compatibility | 同一 IR 的三平台報告及實際 CI；目前未完成 |
| AC-08 五步影片 | FFmpeg 真實影格、fixture 推導 | 真實理解品質未驗，P2 維持停用 |

## 最終交付 Gate

- 固定 20 件低風險範例及獨立預期成果，至少 16 件由非技術使用者完成；目前未有此證據。
- Stage 8 已有明確 JSON 比較、最多兩輪 ModelRepair Provider／UI 接線及獨立範圍／預算授權，
  CSV 算法及參數修復已用真正 Runtime 重播；模型輸出目前仍為 fixture，真實品質及新按鈕視覺驗收待完成。
- Stage 12：Studio 獨立依賴與三平台 CI 尚未接線。現有 packages.yml 使用底座環境收集 tests，
  不可假設它已完整驗證 Studio，也不可用整批跳過 Studio 測試替代依賴配置。
- Stage 13：開發用 open_studio.bat 不等於乾淨安裝交付。尚需發行工具、使用者／開發者指南、
  隱私／限制／升級回滾文件、完整包清單、無私密內容檢查、乾淨安裝啟動及最終 commit 證據。
- 底座與兩個現有流程回歸仍須在最終變更上重測。既有工作樹的 recovery JSON 輸出失敗單列，
  不擅自修改使用者既有底座變更以讓 Studio 看似通過。
- 最終審核須再展開每階段的命名產物與測試要求；本表只提供主索引，不能直接拿來宣告完成。
