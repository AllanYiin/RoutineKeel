# Stage 7–12 驗收證據核對

本文件對照原 `specs/requirements.txt`，不以總測試數代表所有需求完成。
Stage 12 開始收集整體驗收證據，P0/P1 的真人、模型及平台 Gate 仍須完成。

## 核心契約與 DoD

| Stage | 原 DoD／關鍵要求 | 目前可查證據 | 未證明部分 |
|---|---|---|---|
| 7 | 至少兩個固定 Golden Case 在隔離工作區由 flow_base 執行；公開介面不變 | test_runtime_compatibility 的 copy 及多步 DAG；helper 實際 plan/prepare/verify/run；生成包完整性／生成 CSV、JSON Handler 測試 | 整體真實模型與固定 20 例的泛化品質尚未驗收；不是任意 Python／任意 IR 的支援聲明 |
| 8 | 可重現報告、失敗不可發布、修復失敗不破壞前版 | static/replay/report/comparison、修復測試、20 個固定發布故障點與程序中止；ModelRepair 有 fixture Provider＋真實重播 | 整體真實模型品質及其他平台仍待；不能只引用生成器自測 |
| 9 | 有限分支可追蹤、模型串流／預算、無模型的正式流程零呼叫 | test_branches 雙路徑、test_llm_runtime 的 loopback 串流／結構／授權／預算；新發布包 100 次零 registered requests | 真實服務費率及模型品質未驗，不由 fixture 代替 |
| 10 | 影片證據／未知與時間線；P2 可獨立停用 | video ingestion 的實際 FFmpeg 與 VideoDiscovery fixture；一般圖片入口拒絕影片 | 真實影片理解品質未驗，保持 P2 停用 |
| 11 | 非技術使用者用 UI 完成低風險轉換、核准／正式發布／回滾 | 已有 Windows 主畫面證據及 UI model 端到端；新增完整治理、案例、草稿保護與比較視窗 | 新視窗未實機走查，至少 16/20 非技術使用者成功轉換尚無證據；不能標成完整 DoD 已過 |
| 12 | 固定矩陣、相容／安全／基準與三平台；所有 P0/P1 驗收 | §12.1 五項基準已有本機證據，故障與程序中止見 fault-matrix.md | 20 範例、全部 AC、目前底座完整回歸、三平台 CI、真人／模型 Gate |

本輪直接執行 Compiler／生成包／Runtime 相容／生成 Handler／Verifier／Repair／Branch／Runtime LLM
八個測試檔：37 passed in 35.26s。這支持上述本機工程行為，不能當成付費模型或真人操作結果。

## 原指示檔名與實際責任位置

原指示中的部分拆分檔名未原樣建立。下表明列差異，沒有新增空殼檔來充數；
最終文件與維護者交接須使用實際責任位置。

| 原指示 | 實際實作／測試 | 狀態 |
|---|---|---|
| Stage 7 lowering、generator、handlers、package、compatibility、templates；三個命名測試 | 同名模組／目錄及 test_compiler、test_generated_packages、test_runtime_compatibility | 存在；可信舊模板保留，Runtime API 0.2 |
| verifier/artifacts.py | verifier/replay.py 的 output_bytes、comparison.py 及 report.py | 檔名不同；正式產物、指紋與驗收比較已集中在上述模組 |
| verifier/security.py | verifier/static.py、compiler/template_registry.py、handlers.py、ingestion/security.py | 檔名不同；包清單、模板、受限 Handler、路徑檢查有對應責任 |
| tests/test_replay.py | test_verifier、test_runtime_compatibility、test_machine_evidence | 檔名不同；實際重播、成果缺漏／損毀與保存證據分開測試 |
| verifier/branch_tests.py | compiler/branch_lowering.py 與 test_branches | 無同名生產模組；雙路徑驗收位於測試，未把測試器塞進產品 |
| tests/test_llm_budget.py | test_llm_runtime 的 budget／authorization 負例、test_discovery、test_model_repair | 無同名檔；前次嘗試該檔的命令沒有執行任何測試，已改用存在的實際測試檔 |
| project/source/workflow/verification/release_view.py | ui/app.py、layout.py、source_preview.py 及專責 *_dialog.py／project_edits.py | 檔名與分拆不同；UI 經 model／Service 操作，實機 Gate 尚待 |

Stage 7 原文明列「不支援 IR 正確阻擋」及不支援清單，因此不能自創「任意型別／所有第三方工具皆須支援」的 Gate。
同時，明定的 P0/P1 能力不能靠標成不支援跳過。下一步使用固定 20 範例與逐項 AC 去查核實際能力，
保留明確限制並修復驗收揭露的缺陷。
