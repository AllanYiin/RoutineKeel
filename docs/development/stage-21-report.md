# Stage 21 設計期 Pattern 推薦

Studio 由 Registry 取得 L1 metadata，以用途及領域縮小範圍，再讀 L2 埠、正反例、政策與適用條件。L3 子圖需先在同一訪談選用。判定使用本機 deterministic ranking，決策保存於現有訪談交易，綁定請求、版本、上下文指紋、實際回覆和目前核准的 Pattern。

| 實際檢查 | 結果 |
| --- | --- |
| `python -m pytest tests/studio/test_pattern_trigger.py tests/studio/test_pattern_registry.py tests/studio/test_studio_interviews.py -q` | Windows 17 PASS |
| 40 組固定正反例 | Precision@3、Recall@3 均為 1.0。誤觸發率與拒絕後重複率均為 0，模型 token 用量為 0。結果僅適用於本測例。 |
| 適用性 | 單一資料檔或不同資料 schema 無法觸發勾稽。缺少主鍵時顯示待補條件。矛盾、循環及同類品質 Gate 不重複建議。 |
| 純文字宿主回歸 | 驗證讀回、重送、過期決策、主動關閉建議及工作區無正式安裝。 |

回滾可使用訪談中的 `configure_patterns` 停用建議。歷史仍可查，已匯出 Blueprint 由原版本管理。自動化測試的 Pattern 核准及對話均使用明示 fixture。真人任務完成率及跨平台未於本階段測量。
