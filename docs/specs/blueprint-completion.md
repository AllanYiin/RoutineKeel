# 藍圖補強與接續建置

底座將預檢缺口轉成可執行、可恢復且有驗證證據的補強工作。工具、環境與案例未備妥時，Studio 仍可定稿與匯出有效藍圖，底座仍可收為草稿。完成與使用者已授權的工作相符的補強後，底座自動接續隔離試做與交付。

## 邊界

- Studio 保存設計及實際答覆；底座保存來源、補強工作、目標環境的資格證據與候選驗收。宿主呈現具名待辦並執行已獲授權的宿主操作。
- 補強須實際執行已知可安全決定的工作；素材、業務語意、外部授權待確認時，保存進度並先做其餘獨立工作。失敗可重驗或修復，收據不明先查核。
- 來源與衍生版本不可變。候選工具等效且在既有權限內才可自動採用。改變原設計、品質或交付契約時須經 Studio 確認新版本。
- 隔離執行實際候選套件。必要案例、共享 IR／wheel 檢查、候選收據及安裝前重驗均為正式安裝條件。既有線性藍圖與舊入口保留相容。
- CLI、MCP stdio 與宿主用同一服務；長操作可查進度，進度須有證據。私人成果、憑證和原始素材行使既有本機與路徑邊界。

## 階段驗收紀錄

### Stage 0

- 測試涵蓋 Studio 預檢同時回報能力與案例缺口、定稿仍可確認、ZIP 可匯入、草稿尚未試做，以及原本可建置版本正常交付。
- Windows 執行 `flow_base/.venv/Scripts/python.exe -m pytest tests/test_graph_builder.py tests/test_blueprint_install.py tests/test_nested_blueprint_install.py tests/test_capability_binding.py tests/test_capability_resolver.py tests/test_binding_qualification.py -q`，47 passed。
- 本階段只有測試與開發文件差異。沒有遷移或安裝私人工作區；回退可還原本階段檔案差異。

### Stage 1

- 底座 SQLite v7 保存補強工作不可變修訂。CLI 與 MCP stdio 以同一識別碼查詢、重驗和續做，守住請求重送與預期修訂。
- Windows 執行 `flow_base/.venv/Scripts/python.exe -m pytest tests/test_blueprint_completion.py tests/test_db_migrations.py tests/test_mcp_stdio.py::test_blueprint_completion_cli_to_mcp_reconnect tests/test_graph_builder.py -q`，24 passed。首輪還原測試使用錯誤欄位，已修正為實際欄位 `task_id` 並重跑全組。
- 實測利用 v7 升級前備份還原 v6 並讀取既有工作流。停用新入口時保留 v7 證據；若回退到只支援 v6 的底座，留存 v7 副本後使用已驗證的 v6 備份。

### Stage 2

- 唯一可安全試做的本機 Adapter 由底座驗證。候選資格須有資產、環境與案例的相同指紋才能重用；宿主證據由受信清冊取得。無法試做時保留候選與缺件原因。
- Windows 執行 `flow_base/.venv/Scripts/python.exe -m pytest tests/test_blueprint_completion.py tests/test_binding_qualification.py tests/test_capability_resolver.py tests/test_capability_binding.py tests/test_db_migrations.py tests/test_mcp_stdio.py::test_blueprint_completion_cli_to_mcp_reconnect -q`，46 passed。已修正舊遷移測試隱含的版本切片，明確以 v5 建立回復點。
- 低風險資格驗證實際啟動獨立本機解譯器；宿主使用既有測試清冊，未發出供應商請求。回退自動資格入口時保留原收據供既有 CLI 使用。

### Stage 3

- 可執行案例可由已確認的明確斷言補齊，或以具名缺口及實際答覆保存補充案例。底座保持原藍圖與案例意圖，重要變更須由 Studio 匯出有來源關聯的新定稿。
- Windows 執行 `flow_base/.venv/Scripts/python.exe -m pytest tests/test_blueprint_completion.py tests/test_binding_qualification.py tests/test_graph_builder.py tests/test_studio_design_contract.py -q`，27 passed。涵蓋複製素材、內容指紋、Adapter 案例補件及完整 Studio 修訂。
- 回退時停用補件與修訂動作，保留已匯入來源、同一工作中的確認與素材引用。

### Stage 4

- 補強完成後，底座保留試做意圖並啟動本機隔離程序。中斷可重用同一建置識別碼。案例錯誤回到對應補強，成功後依原意圖交付候選包或安裝。
- Windows 執行 `flow_base/.venv/Scripts/python.exe -m pytest tests/test_blueprint_completion.py tests/test_workflow_evolution.py tests/test_graph_builder.py tests/test_blueprint_install.py tests/test_db_migrations.py tests/test_mcp_stdio.py::test_blueprint_completion_cli_to_mcp_reconnect -q`，48 passed。實測獨立程序試做、首次安裝與正式運行，也用缺陷及獨立案例完成既有 Graph 的核准改版。
- 回退可停用自動建置入口，保留候選 ZIP、案例、核准及原底座安裝資料。已完成升級的流程沿用既有升級回復收據。

### Stage 5

- 以 Git 基線加本輪改動製作固定驗收快照。依 JUnit 的測例名稱去重，合計 1,299 項有實際通過的證據。Windows 的 POSIX 略過項由 Linux 補跑，借用相依套件時缺少的 Windows MCP 前置由安裝路徑修復後補跑。
- Windows Studio 全組 566 項通過，既有工作流及音訊全組 105 項通過。底座固定快照先執行全組，再以正確候選包及現行人工校閱答覆重跑，補測組 110 項、MCP 15 項及周邊入口 5 項通過。Linux 專項與啟動器 81 項通過。
- 依同一候選包的雜湊驗證並解壓，在 Linux 建立獨立的底座與 Studio 環境。實際用 Studio 匯出、透過 MCP 續查、完成隔離驗收與安裝，另實跑自動 Adapter 鎖定的工作流。冷啟動證據在 `dist/blueprint-completion-cold-8t7arav4/evidence.json`。
- 可重跑證據為 `dist/blueprint-completion-audit-*.xml`、`dist/blueprint-completion-linux.xml`、`dist/blueprint-completion-workflows.xml`、`dist/blueprint-completion-media.xml` 及 `dist/blueprint-completion-studio-guidance.xml`。測試期間原工作樹有並行的 Documa 和底座通用步驟變更。固定快照界定本次放行範圍。

### Stage 6

- CLI／MCP 操作及回復指引見[藍圖安裝指南](../../flow_base/docs/blueprint-install.md)與[Studio 宿主指南](../../workflow_studio/docs/host-conversation.md)。已從發行 ZIP 核對兩份指南及底座 wheel 內本輪原始碼。
- 本機候選在 `dist/blueprint-completion-audit-p9w74nc7/dist/candidate-completion-final/release`。五個 ZIP 的 SHA-256 已核對。工作區 ZIP 的 SHA-256 為 `6a1eb42678e1a3aa37fe1fbf2626057a8ec64a30f53cf5b7368377b5fd7b1b0b`。
- 在 Linux 解壓最終候選並將該候選的 wheel 安裝到獨立環境，使用已驗證的第三方依賴重跑 Studio 匯出、MCP 續查、資格和案例補強、正式安裝及執行。證據在 `dist/blueprint-completion-final-by5dkfp_/evidence.json`。
- 最終候選也實測 Linux 內附啟動器。以已核對的依賴快取建立全新環境，底座初始設定、MCP 安裝及 Studio 準備均通過，接著完成兩條工作流交付。證據在 `dist/blueprint-completion-cold-ot27rpxk/evidence.json`。
- Windows 候選底座啟動與 MCP 安裝通過。Windows 測試環境的 Studio 冷啟動依賴準備逾時。可用的交付候選與本輪原始碼由固定快照核對，工作樹的並行改動另行整合。
