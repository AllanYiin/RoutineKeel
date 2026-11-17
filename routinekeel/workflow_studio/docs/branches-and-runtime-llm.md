# 條件分支與執行期模型

`control.branch@1.0.0` 是 composite_branch 工具。parameters 中必須有 condition、
when_true、when_false，兩條路徑都只能使用已允許的確定性工具且有相同輸出合約。
條件只支援 file_size_gt、file_size_gte、text_contains、text_equals；數字／文字型別嚴格檢查。
拒絕任意表達式、巢狀分支、外部副作用及輸出型別不一致。
Lowering 將它轉為既有 Runtime 的單一複合 Handler，將真實選擇、條件、輸入 hash 與工具版本
保存至 branch.json sidecar，與主要產物一同接受 ArtifactSet 驗證及快取。

`llm.structured@1.0.0` 是獨立 llm 節點，要求 workspace.write／model.call／secret.read、
network 及 external effect 政策；預設 ToolPolicy 不允許它。
LLMPolicy 規定 model、secret 引用、max_calls、最大輸出 tokens、micro-USD 預算與 Timeout。
parameters 另宣告 HTTPS endpoint、prompt、output_fields、確認過的費率與 pricing_note。
output_fields 支援 string／integer／number／boolean 的精確 JSON 物件，不接受額外欄位。

Compiler 僅在版本含 LLM 時封裝 runtime_llm.py，確定性流程完全不載入它。
Runtime 執行前須明確設定 `WORKFLOW_STUDIO_LLM_AUTHORIZATION=<IR SHA-256>`，
並提供對應 `secret://provider_key` 的 `WORKFLOW_STUDIO_SECRET_PROVIDER_KEY`。
此授權綁定包含模型、端點、提示、輸出契約及預算的 IR 版本；不可由來源資料自行設定。
變更 IR 後需重新確認。沒有授權會在 plan／preflight 阻擋，Runtime Handler 也再檢查一次。

模型文字逐段送至底座 Reporter 的 llm.delta／本機事件／console，最後 JSON 完整驗證才寫出。
不完整串流、錯誤 JSON 型別、拒絕或工具呼叫不會發布產物。不自動重試，也不使用結果快取。
沿用底座公開 LLM Ledger，以 Run ID＋Step ID 分開計算呼叫與保守預算；
流程 snapshot／IR hash／build 紀錄可追溯版本及 Studio 專案。設計期帳本仍在 Studio DB。

已在獨立 Runtime 環境用模擬 SSE 驗證串流、用量、結構、缺授權、預算不足與中斷；
沒有對外發出付費模型請求。Verifier 的預設隔離環境不攜帶模型授權或金鑰，
因此它不能替使用者默默進行需付費的 LLM Golden Case 驗收；這部分需明確的外部驗收授權。
