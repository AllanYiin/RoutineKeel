# 規格整理 v 1.2.0

## Codex / Claude Code 分階段開發計畫

以 [技術規格](technical.md) 的 RK-BS-01 至 RK-BS-07 為驗收索引。現有工作樹已有對應實作，執行各階段時先核對並保留已通過的差異，缺少證據時補驗，發現未達標時才補碼。每一階段都記錄通過、失敗或未測的證據及回退方式。格式和契約的改動只在開發工作區與隔離候選中驗證，正式發布另行授權。

### Stage 0 建立可重跑的基線

**目標與前置條件**　取得現行 Studio、Flow Base 與共享契約的可比較基線。先確認工作樹及發行版本，保留並行修改。由工作區根目錄執行，沿用現有 `uv`、Ruff、pytest、鎖檔、SQLite 與狀態目錄。

**Codex Instructions**

```text
建議貼用方式｜在 RoutineKeel 專案根目錄開工作回合，附本規格連結。
任務範圍｜先確認定稿、執行、交付的現有入口與工作樹基線。
檔案清單｜AGENTS.md、flow_base/AGENTS.md、workflow_studio/AGENTS.md、DEVNOTE.md、兩套件 pyproject.toml／uv.lock、技術規格及 tests/。
具體步驟｜核對套件切分、隔離環境、資料保留方式、MCP／CLI、Documa 設定及既有測試。列出配置前提、現有版本與未測條件。
輸出格式｜一頁基線與指令表，記錄來源版本、測試環境與現有儲存策略。
測試要求｜執行現有 Graph 契約、Studio 定稿和安裝測例，記錄 JUnit 或終端結果。
完成條件｜快照可重跑，舊藍圖及資料契約已分別定位，並行差異可追溯。
```

**Claude Code Instructions**

```text
建議貼用方式｜在 RoutineKeel 根目錄開啟，先讀適用的專案規則與 DEVNOTE。
任務範圍｜為通用步驟改動取基線並建立測試對照。
檔案清單｜專案 AGENTS.md、flow_base/AGENTS.md、workflow_studio/AGENTS.md、套件設定與鎖檔、tests/、docs/specs/base-steps/technical.md。
具體步驟｜記錄既有 Studio、底座、共享契約、SQLite 及隔離候選責任。確認所需本機設定名與可重跑入口。
輸出格式｜可交接的基線、執行命令與既有差異清單。
測試要求｜由根目錄跑定稿契約、Graph 及舊藍圖安裝測例，區分通過、失敗及未測。
完成條件｜可在相同快照重現結果，未動已安裝私人工作區。
```

**風險與回滾**　混用工作樹或載入環境會製造假失敗。保留測試快照與原結果，回退時丟棄本輪隔離產物即可。

### Stage 1 打通定稿到正式成果

**目標與前置條件**　Stage 0 基線可重跑。以一份具名輸出的工作流驗證三個固定節點、確認指紋與本機交付。覆蓋 RK-BS-01、02、06。

**Codex Instructions**

```text
建議貼用方式｜從專案根目錄貼用，附 Stage 0 的版本與測試結果。
任務範圍｜查核定稿自動插入、具名輸出檢核、成果組裝與確認指紋。
檔案清單｜routinekeel_contracts/base_steps.py、execution_review.py、workflow_studio/conversation/design_review.py、flow_base/quality.py、outputs.py、blueprints/adapter.py 與對應測試，均在現有套件 src/ 下。
具體步驟｜核對前置輸入、具名輸出匯入檢核、合格後交付。比對畫面、藍圖與 Runtime，驗證指紋改動會要求重新確認。
輸出格式｜定稿圖、候選驗收收據、成果連結及 ZIP 內容驗證。
測試要求｜測舊確認拒絕、具名成果完整性、品質暫緩與暫存中斷。
完成條件｜合格案例有持久交付，失敗案例保存證據及先前正式版本。
```

**Claude Code Instructions**

```text
建議貼用方式｜附基線和技術規格 RK-BS-01、02、06。
任務範圍｜逐項驗證輸入檢核、輸出檢核及整理交付的設計和執行契約。
檔案清單｜共享契約的 base_steps.py、execution_review.py，Studio 的 design_review.py，底座的 quality.py、outputs.py、blueprints/adapter.py，以及定稿與原子交付測試。
具體步驟｜以 CLI 或 MCP 走初稿、定稿、匯出及候選試做。用隔離工作區核對具名結果、版本清單和 ZIP。
輸出格式｜步驟對照、實際案例及失敗時的證據位置。
測試要求｜竄改確認指紋、拒絕安裝、輸出不合格及組裝故障。
完成條件｜必要檢查與原子提交皆有證據，所有示範在隔離環境內完成。
```

**風險與回滾**　避免將檢核狀態當作已交付。回退時保留定稿與 Run 紀錄，切回上一個已驗證交付版本及套件。

### Stage 2 驗證報表勾稽與計畫重用

**目標與前置條件**　定稿可引用可選步驟，並有兩份可互證的固定測試資料。覆蓋 RK-BS-03。

**Codex Instructions**

```text
建議貼用方式｜附一份對帳案例及技術規格的勾稽規則。
任務範圍｜查核 base.reconciliation.plan 與 base.reconciliation.run。
檔案清單｜flow_base/components/workflow_contracts/ 的 base_steps.py、flow_base/src/flow_base/graph/base_steps.py、tests/test_base_steps.py。
具體步驟｜確認相同版本與規則共享有效計畫。每次使用實際 left 與 right，記錄等值、計數、加總及逐鍵差異。
輸出格式｜計畫指紋、快取命中證據與兩次不同的勾稽結果。
測試要求｜覆蓋十進位容差、等值差異、重鍵、缺欄、無效計畫及規則版次更新。
完成條件｜第二次執行重用計畫，呈現當次差異，品質分支依規則動作。
```

**Claude Code Instructions**

```text
建議貼用方式｜在根目錄附工作流案例與驗收 RK-BS-03。
任務範圍｜用報表流程驗證規則計畫與每次勾稽輸出。
檔案清單｜共享與 Runtime 的 base_steps.py、tests/test_base_steps.py。
具體步驟｜先使用固定比對基準定稿，連跑兩次不同資料，再變更規則驗證計畫失效。
輸出格式｜各情境的計畫指紋、matched 及明細狀態。
測試要求｜測容差邊界、缺項、重鍵及規則改動。證明資料變動不會重用舊報告。
完成條件｜規則和資料的版本影響可獨立驗收，舊結果可按 Run 追查。
```

**風險與回滾**　重用範圍限於計畫。回退到原規則指紋與原藍圖，保留新舊報告區分。

### Stage 3 驗證 Documa 文件與技能入口

**目標與前置條件**　有可執行的本機 Documa、固定 PDF／Office 素材及隔離的受信技能目錄。覆蓋 RK-BS-04 與 v1 技能路徑。

**Codex Instructions**

```text
建議貼用方式｜附 Documa 可執行檔版本與測試資料所在位置。
任務範圍｜驗證 Studio 預覽、文件圖查詢及 Documa graph 技能載入。
檔案清單｜routinekeel_contracts/documa.py、flow_base/graph/documents.py、flow_base/blueprints/bundled_skills.py、Studio ingestion 與 facade、studio_tools.py、tests/test_base_steps.py。
具體步驟｜以真實 PDF／DOCX 查區塊及索引生命週期。用測試技能的受信目錄，核對 graph 依賴、來源及預算證據。
輸出格式｜文件／索引版本、Documa 查詢證據、技能載入圖與例外結果。
測試要求｜索引重用、資料變動、偽造派生欄位、缺 Documa、缺技能目錄及 v1 重建。
完成條件｜Studio 與 Runtime 使用同一個 Documa 整合，真實測試可重跑。
```

**Claude Code Instructions**

```text
建議貼用方式｜附技術規格 RK-BS-04，使用隔離的技能目錄及文件素材。
任務範圍｜從來源匯入到文件查詢，再驗證技能依賴載入。
檔案清單｜共享 documa.py、Runtime graph/documents.py、blueprints/bundled_skills.py、Studio ingestion、MCP studio_tools.py、tests/test_base_steps.py。
具體步驟｜核對輸入檢核後才解析，索引隨執行保存。按 Documa 查詢結果回報區塊證據。測同一技能從設計與執行入口載入。
輸出格式｜真實工具版本、素材指紋、查詢結果及技能來源。
測試要求｜測文件預覽、圖查詢、無文件通過、索引重建、技能候選與缺少受信目錄。
完成條件｜指定文件與技能路徑都有 Documa 的實際回應，錯誤可定位與重試。
```

**風險與回滾**　解析器或技能目錄可能因環境而變。隔離來源與正式資料。回退至先前的藍圖及 Documa 綁定，保留具指紋的原始證據。

### Stage 4 驗證模型審查與有限回修

**目標與前置條件**　有宿主綁定與固定的測試回執。正式模型呼叫另依授權。覆蓋 RK-BS-05。

**Codex Instructions**

```text
建議貼用方式｜附本規格模型資料埠與已核定的回執測試案例。
任務範圍｜驗證 judge、critic、reviser 的型別、授權與用量保護。
檔案清單｜共享 base_steps.py、Runtime graph/capabilities.py、graph/session.py、tests/test_base_steps.py、tests/test_graph_review_external.py、tests/test_graph_iterations.py。
具體步驟｜核對同一成果和批評的綁定、通過後略過修訂、失敗後有限回修。所有使用者可見模型文字採 Streaming。
輸出格式｜回修分支、串流與回執驗證、成本證據及人工等待位置。
測試要求｜測完成與截斷串流、型別錯誤、預算耗盡、取消、人審拒絕及回執不明。
完成條件｜能力綁定及確認資料一致，無效回執或超額行為不得取得成功結果。
```

**Claude Code Instructions**

```text
建議貼用方式｜附驗收 RK-BS-05。正式呼叫前使用既有授權流程。
任務範圍｜從批評到回修及再次驗收，覆蓋模型結果、控制與回執。
檔案清單｜共享 base_steps.py、Runtime graph/capabilities.py、graph/session.py、模型與 Graph 等候測試。
具體步驟｜用宿主回執測試審查、通過後轉送及失敗回修。強制所有使用者可見模型文字採 Streaming，並比對完成指紋與預算。
輸出格式｜案例、實際回執類型、分支結果及重試條件。
測試要求｜覆蓋有限迭代上限、不合法修訂、缺少回執或授權及必要人審被拒絕。
完成條件｜每一分支可觀察，未授權或未驗證的結果不得推進。
```

**風險與回滾**　回修有額外用量且結果可能退步。必要時回到原候選與既有人工審閱路徑，保留原回執及歷史。

### Stage 5 補齊整合、回歸與邊界測試

**目標與前置條件**　前面各切片完成。固定同一套程式快照，完成 CLI、MCP、Runner、舊藍圖及兩套隔離 wheel 核對。覆蓋 RK-BS-01 至 RK-BS-07。

**Codex Instructions**

```text
建議貼用方式｜附前面階段的候選路徑及測試證據，在固定程式快照執行。
任務範圍｜做跨入口端到端、輸入邊界、交付故障、舊資料及安裝回歸。
檔案清單｜tests/test_base_steps.py、test_studio_design_contract.py、test_mcp_stdio.py、test_atomic_delivery.py、test_blueprint_install.py、tests/studio/、tools/release.py。
具體步驟｜用同一份 v2 設計自兩個入口執行，檢查新舊資料與同版共享契約 wheel。適用模型案例保留 Streaming。
輸出格式｜JUnit 檔、快照識別、平台、測試統計、隔離驗收與未測項。
測試要求｜測並行、重試、無 Documa、竄改定稿、預算、ZIP 故障及舊方案。重跑失敗項並保留首輪結果。
完成條件｜所有必要案例通過且每個未測或受阻環境都清楚記錄。
```

**Claude Code Instructions**

```text
建議貼用方式｜以不再切換來源版本的測試工作區執行，附 Stage 1–4 收據。
任務範圍｜完整回歸 Studio、底座、Blueprint、輸出版本、兩隔離環境及 MCP。
檔案清單｜tests/test_base_steps.py、定稿、安裝、交付與 MCP 測試、tests/studio/、兩套件及共享契約的發行工具。
具體步驟｜分別建置兩個環境、核對共享契約與 Documa 測例，執行案例後查實際交付和回執。模型案例須測 Streaming。
輸出格式｜驗收矩陣、版本與每項實際證據位置。
測試要求｜至少包含契約不符、舊藍圖、故障注入、取消、重送及工作區邊界。
完成條件｜每個必要驗收有同一快照上的結果，回歸已修復或指出明確阻礙。
```

**風險與回滾**　常駐主程序與子程序若載入不同契約，會拒絕定稿。停止混合版本的測試，從同版本環境重跑並保留原證據。

### Stage 6 完成文件與交接

**目標與前置條件**　必要驗收已具結果。把操作、限制、實際證據與回退方式交給維護者及使用者。

**Codex Instructions**

```text
建議貼用方式｜附 Stage 5 結果及現行文件，從工作區根目錄執行。
任務範圍｜更新技術規格、白話版、宿主指南與既有交接索引。
檔案清單｜docs/specs/base-steps/、docs/specs/README.md、workflow_studio/docs/host-conversation.md、DEVNOTE.md。長期 Studio 規則使用 workflow_studio/AGENTS.md。
具體步驟｜核對所有能力 ID、資料埠、Documa 設定、模型 Streaming、組裝成果、故障處理及回退方式與實際行為一致。
輸出格式｜三份 Markdown、可定位的驗收證據及回滾位置。
測試要求｜重跑文件連結、白話語言檢查、示意圖及樣例命令核對。
完成條件｜交付可使用、可驗收、可回復。正式發布仍走原授權程序。
```

**Claude Code Instructions**

```text
建議貼用方式｜附最終驗收紀錄，從根目錄讀現行協作規則。
任務範圍｜交付文件與維護資訊。
檔案清單｜docs/specs/base-steps/、docs/specs/README.md、workflow_studio/docs/host-conversation.md、DEVNOTE.md。若專案已有 CLAUDE.md，長期指引可引用相同技術契約。
具體步驟｜核對白話流程與技術規格一致，說明 Documa、本機成果、模型 Streaming 及未測情境。
輸出格式｜可交接的規格包、實測結果與回退說明。
測試要求｜檢查全部本機連結、SVG、白話用字及樣例命令。
完成條件｜讀者能依文件完成定稿、試做及成果查找，必要限制清楚可見。
```

**風險與回滾**　過時指南會導致錯誤安裝或誤判發行狀態。回復文件差異時保留已發行版本的驗收紀錄和規格路徑。
