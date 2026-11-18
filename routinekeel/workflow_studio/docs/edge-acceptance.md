# 例外情境與服務契約驗收

對照原規格 §10、§13.2；測試名稱可在儲存庫根目錄的 `tests/studio/` 查找。
本表以實際斷言區分服務行為、合成 Provider 與未啟用能力，不將工程測試當成真人成功率。

## 證據版本

- `cc12c4a` 的 [run31](https://github.com/AllanYiin/RoutineKeel/actions/runs/44282519400)：
  Ubuntu 與 macOS 各 **583 passed、7 skipped、3 deselected**；JUnit 分別 147.795、128.102 秒。
  下列既有測試已核對該兩平台 JUnit；Windows 完整結果仍需收齊。
- Ubuntu 另執行真正 Tk/Xvfb 操作，**1 passed、0 skipped、3.064 秒**。
  這不是 Windows 原生操作或非技術使用者測試。
- 新增兩個獨立連線同時發布的測試：本機 Windows **1 passed、3 deselected、3.34 秒**，
  `dist/simultaneous-publication.xml`。它不在上述 cc12c4a 的 CI 中，需後續 CI 補跨平台證據。
  發布治理檔案完整回歸另為 **4 passed、9.38 秒**，`dist/governance-concurrent-regression.xml`。

## §13.2 的 17 個例外情境

| 規格情境 | 直接證據與檢查範圍 | 判定／限制 |
|---|---|---|
| 空白輸入、空資料夾 | `test_empty_case_files::test_blank_pasted_description_and_invalid_structured_files_still_rejected`；`test_upload_security::test_folder_size_and_empty_limits` | 拒絕空描述及空資料夾；合法零位元組成果另由實際 Runtime 驗證，不混為無效來源 |
| 影片只有部分操作 | `test_video_discovery::test_video_cannot_confirm_unknown_frames_or_partial_results` | P2；CI 未執行真實影片測試，正式入口預設停用，不能標為通過 |
| 重複匯入 | `test_ingestion::test_duplicate_source_is_idempotent_and_blobs_deduplicated` | 同內容重用 blob，來源版本及歷史依服務契約保存 |
| ZIP 路徑穿越 | `test_upload_security::test_zip_traversal_and_reserved_names_rejected` | 檢查相對跳脫、絕對路徑、Windows 磁碟／保留名稱；不產生外部檔案 |
| ZIP 膨脹 | `test_upload_security::test_zip_bomb_link_duplicate_and_nested_archive_rejected` | 高壓縮比、連結、大小寫重複及巢狀 ZIP 被拒；失敗匯入不留下來源 |
| 文件指令注入 | `test_security_end_to_end::test_untrusted_send_request_cannot_become_executable_approved_or_published` | 惡意來源不能自授權工具；SMTP/socket/HTTP 呼叫為零，舊發布包與成果不變 |
| 讀取其他專案 | `test_ingestion::test_failed_import_is_atomic_and_cannot_cross_project`；`test_ir_versions::test_invalid_ir_and_foreign_evidence_do_not_create_revision` | 跨專案來源／證據不能提交；同名工作流另由跨專案發布測試保護 |
| 依賴循環 | `test_ir_graph::test_invalid_graph_rejected[cycle]` | 靜態拒絕；也覆蓋未宣告依賴、未知埠、重複節點與缺少證據 |
| 模型錯誤 JSON | `test_llm_streaming::test_invalid_or_interrupted_stream_rejected`；`test_model_repair::test_two_malformed_proposals_stop_without_replacing_revision` | 不提交不完整回應；模型修復最多兩次，原專案保持不變。合成 Provider |
| 自動修復持續失敗 | `test_repair::test_two_failed_repairs_stop_without_changing_acceptance` | 三份可讀報告、兩輪修復後停止；固定案例指紋不變 |
| 生成時斷線／失敗 | `test_acceptance_lifecycle::test_interrupted_stream_is_visible_and_reopens_last_good_candidate`；`test_operation_events` | 控制器顯示中斷、前版重開；生成失敗記錄最後階段，完成事件與成果同交易。真實 Provider 未驗 |
| 同時點兩次發布 | `test_governance::test_simultaneous_publication_from_two_connections_creates_one_release` | 兩個 SQLite 連線在真正 OS lock 前同步競爭；兩次取得同一結果、正式 manifest 只寫一次、歷史只有一筆，生成包能由 Runtime 執行。本機已過，跨平台待最新 CI |
| 發布途中中斷 | `test_publication_process_crash`；`test_edge_cases` | 三個真實程序中止點及 20 個固定故障點；核對完整 manifest/wheel、舊成果與歷史，詳見 [故障矩陣](fault-matrix.md) |
| 外部動作結果不明 | `test_release_recovery::test_missing_target_is_not_assumed_unapplied`；`test_unknown_files_are_preserved_and_still_block_overwrite` | 本機發布狀態不明時阻擋重寫、提供核對；外寄等遠端副作用適配器尚未支援，不能宣稱已驗其結果核對／重試 |
| 修改已核准流程 | `test_governance::test_unverified_revoked_and_changed_project_cannot_publish`；來源／案例／工具版本失效測試 | 舊核准不能用於改過的內容；已發布版本仍保留 |
| 重跑保留歷史 | `test_verifier::test_independent_verifier_replays_records_and_repeats_safely`；`test_end_to_end::test_hundred_different_inputs_keep_published_outputs_and_ids_isolated` | 每次新 run_id，先前報告可重讀，100 次產物指紋與 ID 隔離；範圍為固定確定性包 |
| 跨平台依賴不可用 | `test_platform_matrix::test_same_canonical_ir_has_platform_reports_and_runs_on_actual_host`；`test_ir_contracts::test_missing_tool_wrong_platform_and_type_rejected` | 缺少工具／平台被拒；同一固定 IR 在實際 runner 執行，不靜默替換算法 |

## §10 共用服務入口

HTTP 形式在規格中描述操作語意，沒有要求常駐 HTTP Server；本版為本機 Python Service。
CLI 目前提供 `ui`、`projects`、`status`，不能宣稱每個服務都有同名 CLI 子命令。

| 規格操作 | 本機責任位置 |
|---|---|
| 專案建立／讀取／更新／軟刪除 | `StudioService.projects` → `ProjectService.create/get/update/delete`；`archive/restore` 與 UI 封存流程 |
| 來源匯入／描述／替換／移除 | `StudioService.sources` → `SourceService.import_path/import_text/describe/remove`；替換沿用 source_id、另存版本 |
| 分析 | `StudioService.discovery` → `DiscoveryPlanner.prepare/run`，送出前綁版本、範圍與預算 |
| 事件查詢 | `llm.streaming.EventStore.read(project_id, after=sequence)`；UI 經同一服務資料庫讀取 |
| 保存 IR／生成／驗證 | `StudioService.save_ir/compile/verify`；原子版本、工具政策、固定案例及 Runtime 重播 |
| 人工批准／發布 | `governance.approvals.Approvals.approve`、`governance.release.Releases.publish`，接受同一 StudioService 並重核指紋 |

`StudioError.as_response()` 提供 `ok/error/code/message`。個別拒絕測試有精確 code 斷言，
其餘測試只證明拒絕與資料保護，不將其誇大成 §13.1 每個錯誤文案、details 欄位與 UI 呈現皆已完整驗收。
真實模型、真人操作、未啟用 P2，以及其他格式的完整原生預覽矩陣仍依 [需求審核](requirement-audit.md) 追蹤。
