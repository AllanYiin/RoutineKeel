# Stage 24 Adapter 資格與環境鎖定

採用前依同一份藍圖、Adapter 精確版本與作用域，分別檢查介面及業務行為。兩道檢查均 PASS 才保存包含版本、環境和收據指紋的 Lockfile，試做及正式安裝再核對同一份鎖定。

- 底座 SQLite v006 保存不可變 Adapter 收據、環境鎖定及另版降級核准。環境檔案以指紋命名，先原子寫入後更新 SQLite 指標。Lockfile 不包含原始測例資料或憑證。
- Windows `tests/test_binding_qualification.py`、`tests/test_capability_resolver.py`、`tests/test_db_migrations.py` 共 29 PASS。實際完成本機 Adapter 資格、候選、安裝及執行，並驗證鎖定竄改、資格失敗、NOT_RUN、另版降級與 v005 備份還原。
- Studio 合成、擷取與 Registry 回歸 17 PASS。宿主授權觀察及重連使用 fixture，工作進入具名待辦。未連線時拒絕領取，重連仍使用相同待辦 ID。模型版本更換後原環境鎖定失效。

本階段未呼叫實際第三方模型、付費服務或私人 MCP，也未測真人終端。需要外部行為資格時，若沒有綁定當次環境、案例與人審的實際宿主觀察，回報 NOT_RUN 並阻擋安裝。

回滾以相同工作區的 v005 遷移前備份和原 wheel 為準，先在空目錄核對雜湊與完整性。保留新版資料庫及鎖定檔以供查證。
