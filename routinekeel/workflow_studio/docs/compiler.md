# Compiler 與 Runtime 映射

`StudioService.compile(project_id, revision_id, tool_policy)` 只編譯已保存的 ir_ready 等有效版本，
重新查核當前工具政策、IR、受控實作及參數，產生候選 package／ZIP／可重建 source 專案。
成果放在 Studio 自己的 `projects/<id>/builds/<build_id>`，不安裝至正式流程目錄。
每次 build 有獨立識別碼、IR hash、ZIP hash 與 mapping；同一 IR 的 wheel bytes 可重現。

目前支援單一檔案輸入、線性／靜態 DAG、四個受控確定性工具及其本機產物。
單檔上限 10 MiB。其他節點種類、多檔輸入、任意新程式、未實作的重試或非終端交付，
明確回報 IR_UNSUPPORTED_FEATURE；不變更 Runtime 語意或把不支援功能偽裝成成功。

Lowering 會拓撲排序、加入具名輸入 Step，讓後續快取包含真正輸入指紋。
每個 Step 的 primary 是帶 payload hash 的 result.json，檔案依照 IR 相對路徑保存，
output_artifacts 宣告 sidecar，交由既有 Runtime ArtifactSet／staging／版本交付處理。
所有輸入引用和前後置條件都映射成受控產物讀取，不使用 eval 或任意命令。
Handler 檔案是固定可信模板；模型內容僅寫入 compiled.json，不插入 Python 程式字串。
參數變更必須回 Studio 新增 IR 版本，不接受未驗證 overrides。

候選流程包包含符合 runtime_api=0.2 的 flow.json、PEP 427 wheel（含 RECORD）、
README、workflow.ir.json、PROVENANCE；source 專案包含 pyproject、flow.template 與 Adapter。
wheel 只依賴已安裝的 flow_base，不匯入 workflow_studio。
既有 `tools/release.py.build_project` 已實際從生成的 source 再建置成功。

實測透過 WorkflowService plan／prepare／verify／run_workflow_sync 執行兩個固定案例，
取得 run.started、step.started、step.finished、delivery.published、run.finished。
同輸入重跑有新 run_id 並命中快取；不同輸入使用新的輸出位置，避免覆寫其他工作。
Runtime 直接模型 API registered_requests 為 0；這不代表開發對話本身沒有 token 成本。

「可編譯並執行」仍不代表通過業務驗收。Stage 8 獨立 Verifier 才判定候選包是否符合 Golden Cases。
