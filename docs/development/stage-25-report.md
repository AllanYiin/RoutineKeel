# Stage 25 Pattern 改善與回歸驗證

底座從實際 QualityAssessment、ReviewFeedback、Session 和已安裝 Graph 讀取來源。Studio 保存來源指紋、修正前後差異、影響工作流與不可變提案修訂。分類區別疑似通用缺陷、工作流設定、Profile 偏好及 Adapter 差異。單筆弱訊號只保留供校閱。

`python -m pytest tests/studio/test_pattern_evolution.py -q` Windows 2 PASS。兩條已安裝流程各有真正的執行和回饋，先證實一條受影響的新候選回歸失敗並阻止 Pattern 核准。修正後使用同一 Builder 重跑兩條流程、原測例和獨立反例。比較收據保留各 Run 的版本證據、試做封裝、用量和品質結果。核准新 Pattern 後，兩條已安裝工作流仍能實際執行原版。

Studio schema 18 新增改善提案及修訂。回歸用 Blueprint 標為 `pattern_rehearsal`，正式安裝和套用更新會拒絕。若要採用新 Pattern，另建工作流版本、重新試做並確認。回滾可沿用現有安裝及舊 Lock；資料回復使用遷移前備份與前版。真人核准及外部模型仍未執行。
