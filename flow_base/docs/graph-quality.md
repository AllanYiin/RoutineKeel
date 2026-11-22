# Graph 成果的驗收與交付

工作跑完後，對話可以分別回答「處理完了嗎」、「結果合格嗎」與「正式版在哪裡」。`workflow_quality` 的 preview 提供具名成果、各項量測、結果指紋及預覽識別碼。只有符合品質契約的版本會更新正式交付指標。待審或未達標的版本保存在該次 Run 的獨立預覽，先前正式成果仍可取用。

藍圖的 `quality_contract.evaluators` 為具名、版本化的規則。底座提供等值、長度、數值範圍與 JSON Schema 檢查。需要領域判斷時，可引用宿主已核准且實際安裝的離線品質評估器。必備評估器缺失會留下 not_run 結果並阻擋驗收。規則可指定 severity 與 on_failure，invariants 中的規則一律適用硬性條件。

使用者可在同一段對話接受、拒絕、修正或撤回回饋。每次操作綁定 assessment_id、預覽內容、revision、request_id、理由、標籤與作用範圍。修正會產生新版本並重新評估，歷史回饋保留原成果及修正後成果的指紋。若已在藍圖指定某個人工校閱節點負責最終判定，可在 review_policy 設定 approved_node，以該次已完成的校閱作為依據。

可調參數由 `personalization.parameters` 的 JSON Schema 限定。設定檔分為 candidate、approved 與 archived，執行時可指定已核准的 profile_id 與 profile_revision。業務規則或品質硬條件的改動要建立新藍圖。
