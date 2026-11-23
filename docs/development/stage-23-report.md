# Stage 23 可攜能力解析

Pattern 宣告抽象能力及必要性、替換模式、執行位置、版本和限制。Resolver 從底座內建工具、受設定位置的宿主清冊，以及可由套件元資料核對的本機 Adapter 產生候選。替換候選仍需資格驗證，缺必要能力時沿用既有 Builder 受阻狀態。

Windows 實際建置並在測試環境安裝 `routinekeel-data-adapter-1.0.0` wheel，提供本機 CSV 讀取及 Decimal 勾稽。宿主表格清冊使用 fixture。未呼叫 Google Sheets 或外部模型。

`python -m pytest tests/test_capability_resolver.py tests/test_pack_contracts.py tests/test_graph_builder.py tests/test_graph_review_external.py -q` 最終 53 PASS。首次執行使用工作區原有的 Studio 開發環境，既有匯出案例回傳 `STUDIO_UNAVAILABLE`。改用本次鎖定環境後重跑整組通過。報告為 `dist/v130-stage23-base.xml`。

測試涵蓋固定版本缺席、清冊過期、權限撤回、時效、Decimal、離線與可選降級待決定。Runtime 的具名宿主待辦與回執沿用原契約。Excel 實作與真人宿主操作未在本階段測試。此階段無 SQLite 遷移。回滾時可使用前一階段底座與 Studio，來源 Pack 維持原版。
