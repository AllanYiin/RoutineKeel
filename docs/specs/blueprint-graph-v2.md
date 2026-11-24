# Blueprint Graph v2

底座的 `install-workflow --archive` 可匯入含單一根目錄及 `blueprint.json` 的設計包。不可執行的設計、原始 ZIP 與 SHA-256 會保存成草稿。Graph v2 格式與 Studio Workflow IR 1.0 分別有版本，既有 Studio IR 使用既有入口。

完整欄位見[套件內 JSON Schema](../../flow_base/components/workflow_contracts/src/routinekeel_contracts/blueprint_v2.schema.json)，包含人工校閱與兩種有限子圖。可用[分支與匯合範例](../../tests/fixtures/graph_v2/route.json)測試匯入。埠宣告型別，輸入綁定 `input://名稱` 或 `artifact://節點/埠`。資料引用、`depends_on` 及 switch 控制邊共同建立 DAG。同一批可執行節點按 ID 排序，檔案中的排列順序不影響結果。

未標示 `graph_contract_version` 的 v2 圖沿用 2.0 的每層 200 節點上限。2.1 可容納最多 401 個節點，讓合法的 200 步 v1 流程保留每步一個校閱點及最後的交付匯合。v1 的 `is_step` 轉換為該步的人工工作，步後校閱與既有 Recipe 共用同一判斷。

2.2 增加物件欄位的有型別條件、明確的 default／error 出口，以及 all_done 的具名備援輸入。執行時只啟用選中分支；另一支會留下 skipped 紀錄，嘗試次數維持零。selected_branch 只讀取實際選中路徑的產物。成功的決策會保存來源、輸入指紋與選路，重新開啟沿用原決策，修訂上游才重新評估。

逐項處理依輸入順序執行，每項有穩定識別碼、輸入指紋與子工作檢查點。失敗項目可單獨重試，collect_errors 可保留每項結果及錯誤。repeat_until 可從已符合條件的初始值直接結束；達上限仍未符合條件時，依設計停止或留下關鍵校閱。用量上限與實際用量分別記錄。模型項目須收到完整的文字增量與相符的宿主用量回執才可進入下一輪。

- switch 對具名來源做有型別比較，各有 true、false 出口，條件錯誤會失敗或等待人工。
- selected_branch 要求兩條路徑各能提供所有具名交付，型別及控制路徑必須對應。all_success 可轉送確定存在的輸入，all_done 可用作只等待終止狀態的屏障。
- foreach 綁定有元素型別的陣列，子圖接收 item、交付 result；repeat_until 的子圖透過 value 傳遞狀態。兩者均有最大數量、時間、總工作、模型呼叫及成本限制。
- v1 匯入保留原始來源及合約。轉換用穩定識別綁定舊埠名，保留人工校閱點、原始步驟順序、具名輸出和 SHA-256。尚待選擇的舊工具標為 unresolved。無法等價表達時可在草稿 findings 查詢原因。

匯入只驗證宣告、建立草稿。以 `resolve-blueprint --draft-id <ID>` 取得能力指紋，交給底座 Builder 後才建置候選工作流。底座把候選工作流放進拋棄式驗收工作區，執行確認過的案例；成功後，正式安裝重新核對收據及套件指紋。原始藍圖沒有 run 入口。

Graph 工作保存各節點的狀態、嘗試次數、輸入輸出指紋與事件紀錄。用 `blueprint-task <session_id> status` 查詢，`next` 可從檢查點續跑。一般步驟不需要人工答覆；具名校閱可以同時等待，再逐項呈現於對話。回修指定 step 與目前 revision，只撤回該節點及依賴它的後代。可用原始 v1 資料與舊已安裝流程回復，保留新版工作狀態供相容版本重開。
