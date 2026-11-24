# Pack、Pattern 與跨環境綁定

Pack 是設計及驗收案例的發行單位。可重用 Pattern 會在 Studio 訪談中呈現適用條件、輸入需求與品質規則。採用後，Studio 將它展開到 Blueprint 草稿，仍經同一 Builder 試做，再以核准的目標環境安裝。執行中的工作流使用安裝時鎖定的版本。

## 在本機查閱與採用

1. 核對 ZIP 的 SHA-256。Studio 以 `pack register` 登錄檔案，檢查內容、來源與相依。候選在本機互動終端核准後，Pack 才能供採用。
2. 使用 `pack qualify` 試做指定 Pattern，查閱「介面」、「行為」、「模擬回執」與「實際模型」的證據，再核准該版本。模型替換若欠缺已取得授權的實際觀察，底座顯示 NOT_RUN。
3. 在訪談中查閱 L1 簡介及 L2 契約。若同意採用，宿主記錄這次答覆，再顯示 L3 子圖、資料對照與合成差異。使用原有 Blueprint 預覽及確認入口匯出。
4. 用 `flow_base resolve-blueprint --draft-id <識別碼>` 核對目標環境，選擇願意試做的 Adapter。以 `flow_base qualify-blueprint --draft-id <識別碼> --selections <本機 JSON>` 預覽。選擇檔以節點 ID 對應具版本的 Adapter 參照。確認後加上 `--execute`，在本機終端核准此份試做。完成後再走原有候選建置與安裝。

暫緩、拒絕或停用設計建議都保留歷史。無可比較資料時，Studio 會略過數據勾稽建議。若新環境只觀測到同類 Adapter，會先顯示 `needs_qualification`。宿主離線時留存具名待辦。環境、權限或模型版本改變時，重新查核必要收據。

## 參考 Pack 與實作

| 資產 | 內容 |
| --- | --- |
| Text 1.0.0 | 自然改寫、保留數字與引用、指定人工校閱。需符合該環境的宿主模型品質與核准規則。 |
| Data 1.0.0 | CSV 讀取契約、以識別鍵及 Decimal 核對資料、正式交付 Gate。 |
| Data Adapter 1.0.0 | 獨立的本機 Python wheel。對明確提供的 CSV 文字及表格做確定性讀取和勾稽。 |
| Translation Review 1.0.0 | 翻譯初稿的批評與修訂子代理。可嵌在現有翻譯者之後。 |
| Translation Review Loop 1.0.0 | 修訂後交回批評者複審，最多兩輪。達上限仍未通過時等待人工處理。 |

可選 Pack 與 Data Adapter 位於具名候選的 `domain-packs/` 並附獨立 `SHA256SUMS`。只有要在實際環境執行讀取與勾稽時，才準備需要的 Adapter。舊底座及原有範例沿用 `release/` 的套件和校驗清單。

### 翻譯子代理試做

範例工作流為「翻譯者 → 批評者 → 修訂者」。`text.translation_review@1.0.0` 僅封裝批評者與修訂者。Studio 在 `translation` 任務有 `translation_quality_review` 意圖、可提供原文、初稿和目標語言且允許宿主處理時，會建議這個 Pattern。採用後，將 `source` 與 `target_language` 接到工作流輸入，`draft` 接翻譯者輸出，最後交付接到修訂者的 `result`。

兩個能力的節點參數各自保存 `agent_role`、`context_scope` 和 `instructions`。宿主 Adapter 應依角色委派獨立子代理，傳入當次所需資料。批評者產出結構化 `issues`，修訂者同時取得原文並逐項核對批評後再改稿。角色定義由版本化 Pack 持久保存，每次執行依待辦和檢查點交接。

需要修訂後回審時，Studio 可依 `translation_iterative_review` 意圖建議 `text.translation_review_loop@1.0.0`。把翻譯者的輸出接到 `state`，其物件包含 `source`、`draft` 和 `target_language`。每輪由批評者檢查當前譯文。批評者接受時保留當前譯文，否則交修訂者更新 `draft`。兩輪後仍未獲批評者接受時暫停並等待人工處理。通過的 `result` 物件中的 `draft` 為最終譯文。

本機驗收以 `uv run --locked --project workflow_studio --group dev python -m pytest tests/studio/test_translation_review_rwp.py tests/studio/test_translation_review_loop.py -q` 執行。案例涵蓋漏譯期限、數值誤譯、正確初稿與空白資料。隔離回放檢查設計建議、採用、嵌入、三步接線、第二輪回審及上限處理。正式建置仍需要目標環境中的實際宿主 Adapter 與資格收據。

### 宿主觀察與疑難排除

本機工具須在 Runtime 環境實際安裝並由受信清冊觀察。位於宿主的 MCP 需由已連接的宿主提供工具版本和契約。清冊沒有列出可用工具時，先檢查連線或已安裝套件。工具內容、權限、執行位置及 Provider 版本均會影響環境指紋。

底座從啟動環境的 `ROUTINEKEEL_HOST_CATALOG` 讀取清冊。該路徑須位於工作區外，清冊含格式版本、有效期限及工具清單。選用 Data wheel 安裝於 Runtime 後，可在受信清冊宣告 `routinekeel.csv_read` 或 `routinekeel.decimal_reconcile`。本機工具的執行位置是 `local_runtime`，套件是 `routinekeel-data-adapter`，版本是 `1.0.0`。工具清冊須與安裝套件、資料埠、權限及蒐證範圍相符。資料包本身只攜帶 `tabular.read` 等抽象需求。

宿主實際資格收據須核對已確認的請求、作用範圍、模型版本、案例和逐案輸出指紋，並提供介面、權限、逾時、副作用及預算的測量。合成回執供工作流試做。若必要資格顯示 NOT_RUN、FAIL 或鎖定失效，保留已安裝版本，重新核對目標環境。

## 維護、升級與回退

以 `uv run --locked --project flow_base --group dev python tools/release.py --candidate <名稱> --reference-packs` 建置完整本機候選。執行測試時用該候選的 `release/` 指定封裝與兩個既有範例。

Pattern 改版須保存原回饋與受影響工作流、舊版與新版試做及差異指紋。比較候選沒有正式安裝權限。候選通過後先核准新 Pattern，再依原工作流換版程序建立、比較及核准新版。遇到失敗時可用舊 Blueprint、Lockfile、套件與 SQLite 備份，先在空目錄驗證能還原。
