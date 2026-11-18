# 文字與 Mermaid 流程發現

`StudioService.discovery(provider, budget, tool_policy)` 回傳 DiscoveryPlanner。
先以 `prepare(project_id, source_ids)` 取得實際傳送內容、來源版本、目的端、模型與 request_hash。
對外模型必須在使用者看過範圍後，以同一 hash 呼叫 `run(..., authorized_hash=...)`。
來源、Project version、目的端、模型或預算變更會使原確認失效；沒有確認不發出請求。

Project goal 使用 `description` 與 `success_criteria`。資訊不足時可以儲存專案草稿，
模型應產生 questions；不得擅改成功標準再宣稱候選流程通過。
目前接收文字與 Mermaid；每次模型分析只提供使用者選取的來源副本、工具簽章與 IR schema。
來源置於 user 資料區，政策置於固定 system 訊息；不提供工具呼叫能力。

DiscoveryResult 為 analysis、ir（可為 null）、questions。
所有模型文字透過 analysis.delta 逐段輸出並持久化；只有完整完成標記與嚴格 JSON／IR
驗證通過後，才以交易提交 Revision、usage 與 analysis.completed。
必要資訊不足時保存 needs_clarification；中斷／取消／無效結果不取代上一個有效版本。
模型不能自行宣告 verified／human_confirmed、引用未提供的來源，或改寫使用者成功條件。
EventStore 支援 sequence cursor 重讀已保存的事件；不把未完成文字當成正式 IR。

Provider Protocol 可替換。內附 ChatCompletionsProvider 使用 HTTPS SSE，不自動重試、
不跟隨重新導向、不呼叫模型提供的工具；API key 僅從指定環境變數讀入授權標頭。
使用 `stream=true`、`stream_options.include_usage=true` 與 `max_completion_tokens`，
依 [OpenAI Chat Completions API](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
及 [串流回應契約](https://developers.openai.com/api/reference/resources/chat)（2026-11-17 查核）。
不同相容服務或模型可能不支援相同參數，須另測，不能宣稱全部支援。

Budget 使用整數 micro-USD，由使用者提供確認過的 input／output 每百萬 token 費率。
呼叫前以 UTF-8 payload bytes 加固定餘裕及最大輸出量作保守估計並保留預算。
無 usage 收據時保留 reservation，token 標記 unavailable；cached tokens 包含於 input，
不重複加總。設計期／執行期帳本分開；實際服務計費仍依模型供應商，不把估計當帳單。

Stage 5 已驗證固定 fixture Provider 與模擬 HTTP SSE 傳輸，未發送付費真實模型請求。
因此這些測試證明串流、驗證及失敗處理，不能當成真實模型流程推導品質或 20 件業務驗收證據。
API key、實際模型／費率、允許外傳範圍與實測預算需在真正呼叫前配置。
