# 根據業務試做修復

工作臺的「根據試做差異修復（最多兩輪）」使用與分析相同的 Provider 介面，
但具有獨立的資料範圍確認。這是設計期模型呼叫，不會自動核准或發布。
底層入口為 `StudioService.model_repair(provider, budget, policy, runtime_python=...)`。

`prepare(project_id, report_id, cases)` 只接受目前流程版本的失敗報告，且該報告須已通過靜態安全檢查。
案例清單必須與原試做 disclosure hash 完全一致；不能移除較難的案例再稱為同一次修復。
prepare 不呼叫模型，回傳不可變 `PreparedRepair`；`scope_json` 是可檢查的資料範圍，
`request_hash` 綁定專案版本、套件、報告、來源、比較規則、目的端、模型、工具政策與預算。
外部 Provider 的 `run` 必須收到一致的 `authorized_hash`。

會傳送目前 IR（含生成程式、證據與參數）、固定案例的完整輸入及預期文字、比較規則與差異。
後續輪次會包含前輪修正與重測差異。其他匯入來源、Runtime console log、機器路徑與憑證不會被額外收集。
目前此入口接受 UTF-8 案例，每案例輸入與預期合計上限 128 KB，整體準備資料上限 256 KB；
超過限制或二進位案例會明確拒絕，不假裝已完成修復。這些限制仍須對照最終案例集驗收。

每次操作最多兩次模型呼叫，無效 JSON 或無法編譯的提案也計入兩次上限。
傳輸錯誤、使用者停止、預算不足、未知業務規則、權限變更不自動重試。
未知規則以 `repair.needs_clarification` 事件保存並顯示待確認問題。
只有完整串流且符合 schema、工具政策、編譯支援與生成 Handler 合成測試的提案才能保存新 Revision。
每個有效提案重新封裝、通過靜態檢查後，在獨立 Runtime 工作區重跑同一組案例。
第二輪接續第一輪 IR，固定目標、輸入、證據、交付物名稱、案例及成功標準不得改變。

若原流程有 Runtime LLM，確認範圍也列出目的端及兩輪全部案例的保守試做預算。
新提案不能增加模型節點、更換端點／模型／secret、提高單次預算或更改費率；
已授權的記憶體憑證僅在範圍檢查後，重新綁定新 build 的 disclosure hash。
設計期費用記入 Studio `design` 帳本，仍受專案累計次數／預算限制；Runtime 試做用量由底座記錄。
目前 UI 的執行期憑證仍須匹配工作臺所設定的目的端及模型，不會將一個 Provider 的金鑰傳給另一端點。

`repair.started/delta/proposed/verified/completed/failed/round_failed/needs_clarification` 事件保存於既有事件表。
完成事件包含 build_id、report_id、輪次與真實重測結果。程式中斷不會把半份模型內容標成有效流程。
修復期間專案、案例、工具政策或模型設定改變會拒絕後續提交。
使用者停止時不開始新呼叫；已開始的隔離試做先結束，不宣稱已即時終止 Runtime。
原正式發布包保持不變；成功後仍需使用者核對成果並核准發布。

目前證據是測試 Provider 與真正底座重播（包含 CSV 邊界條件及金額參數的累積兩輪修復）。
Runtime LLM 授權測試使用替身重播，不是付費模型品質驗收；未宣稱能自動推斷未知業務規則，
也不能以模型看到的案例通過取代獨立未見資料與使用者驗收。
