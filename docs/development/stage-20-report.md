# Stage 20 通用化、品質契約與獨立試做

Stage 20 同時核對來源 Pattern 的 Graph、埠、能力需求、政策及品質約束。來源容差不相容會要求變體。明確對應的可配置參數轉為型別化 Profile 欄位，原設定值留在來源版本。

| 實際檢查 | 結果 |
| --- | --- |
| `python -m pytest tests/studio/test_pattern_qualification.py tests/studio/test_pattern_registry.py tests/studio/test_pattern_extraction.py tests/studio/test_blueprint_export.py tests/studio/test_migrations.py -q` | Windows 30 PASS |
| `python -m pytest tests/test_pack_contracts.py tests/test_graph_builder.py tests/test_mcp_stdio.py -q` | Windows 41 PASS |
| 文字保真 | 同一 Builder 的隔離工作流實際輸出兩組有來源指紋的正向數字／引用案例，另測反例與空值。 |
| Decimal 勾稽 | Builder 在隔離工作區重播測例回執，標示 simulated；Studio 對實際交付的判定另以 Decimal、主鍵、重複鍵及缺值獨立驗算。不連線時的預檢實際回報能力受阻。 |
| 核准門檻 | Fixture 回執即使通過輸出比對，若 Decimal 驗算不同仍保存 FAIL，且無法核准。成功試做也要另有綁定指紋及修訂的真人核准。測試中的核准使用明示模擬。 |

`flow_base/graph/pattern_trial.py` 使用現有 Builder 與 Verifier。Studio 保存 Pack、Pattern、來源案例、Run、實際輸出與 Builder 收據指紋。必要反例、邊界和不同正向業務案例須覆蓋；合成案例不能充作兩組正向。Profile 型別與控制邊界缺少時回報 NOT_RUN。此階段利用 Stage 18 的試做表，未新增資料庫遷移。

未執行真人核准、真實模型、外部 API 或 macOS/Linux。現階段以本機編寫的參考業務案例進行驗證，尚未取得實際使用者業務樣本。回滾可重建前一階段 wheel 並保留 Registry，以歷史收據辨識產生它的版本。
