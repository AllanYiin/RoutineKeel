# DEVNOTE

## SNAPSHOT — 當前狀態

最後更新：2026-11-24T23:22+08:00
交接狀態：verified

### 目標與授權範圍

依 /prompts:devloop 執行底座通用步驟與 Documa 修改規格 Stage 0–6。

### 目前進度

底座通用步驟 Stage 0–6 均已完成。暫緩交付與前次成果分隔、異常模型回執正常結束、Documa 與模型診斷補齊。最終固定快照 1,314 個不同測例通過，隔離安裝後實際交付勾稽成果。

藍圖補強 Stage 0–6 亦已完成，包含資格鎖定、案例補件、隔離試做及安裝／升級續接。驗收與回退記錄見 `docs/specs/blueprint-completion.md`。

### 下一步

本輪授權的開發與本機驗收無待辦。正式發布、私人已安裝環境及正式模型品質驗收另依原授權。

### DECISIONS

- 預檢缺口屬候選試做，藍圖設計與匯入依原契約繼續。底座保存補強工作並自動續行。
- 有效工具與案例證據可獨立補強；新業務版本、宿主副作用及既有版本升級沿既有核准與回歸入口。
- 本機候選以 HEAD 加本輪改動製作固定快照，保留共用工作樹的並行修改。
- 清除前次具名成果只作用於本次暫存副本，正式上一版及其回退入口保留。

### LANDMINES

- Windows 候選底座與 MCP 可用，Studio 冷啟動依賴準備在測試環境逾時。Linux 實際冷啟動及最終 wheel 交付路徑通過。
- 回退到底座 v6 前先留存 v7 資料副本，並使用已實測還原的遷移前備份。
- 模型測試使用本機宿主測試回執，正式模型品質依實際綁定及授權驗收。
- 本輪 Windows 封裝使用兩個獨立 wheel 環境；完整原生啟動器冷啟動與非 PDF／DOCX 解析品質保留各環境驗收。

### 證據索引

- docs/specs/blueprint-completion.md
- flow_base/docs/blueprint-install.md
- workflow_studio/docs/host-conversation.md
- dist/blueprint-completion-final-by5dkfp_/evidence.json
- dist/blueprint-completion-audit-p9w74nc7/dist/candidate-completion-final/release/SHA256SUMS
- docs/specs/base-steps/verification.md
- workflow_studio/todo.md
- dist/base-steps-devloop-final-audit.json
- dist/base-steps-devloop-final-integrity.json
- dist/base-steps-final-snapshot-ib7yhhrs/dist/candidate-base-steps-devloop-final/release/SHA256SUMS

### VALIDATION

- 固定快照回歸 — PASS：依實際 JUnit 去重後 1,299 個不同測例通過，包含 Studio 566、Linux 81、工作流 105，以及 MCP、封包與資料還原補測。
- 最終本機 ZIP 與 wheel — PASS：五個封包雜湊及本輪 wheel 原始碼核對一致。最終獨立環境實際執行 Studio 與便攜能力兩條交付路徑。
- Windows Studio 原生冷啟動 — NOT_RUN：底座啟動及 MCP 安裝通過，Studio 依賴準備超過測試時限；Linux 已覆蓋完整發行包路徑。
- 底座通用步驟 Stage 0–2 — PASS：29、72、5 項。命令及回滾見 docs/specs/base-steps/verification.md。
- 底座通用步驟 Stage 3–4 — PASS：真實 Documa 4 項，模型回執及 Graph 30 項。見 dist/base-steps-devloop-stage3.xml、dist/base-steps-devloop-stage4.xml。
- 底座通用步驟 Stage 5 最終快照 — PASS：1,314 個不同測例，涵蓋 Windows Studio、底座、工作流、Linux 平台補測及跨宿主。見 dist/base-steps-devloop-final-audit.json。
- 底座通用步驟 Stage 6 文件與交接 — PASS：文件連結、SVG、白話檢查、隔離 Studio guide／底座 catalog 及 Git whitespace 檢查通過。

# HISTORY


- 2026-11-17：完成 Stage 0–2；Stage 3 開始。
- 2026-11-17：Stage 3 實作與驗證完成，含 Client 退出後獨立 supervisor；準備進入 Stage 4。
- 2026-11-17：Stage 4 實作與驗證完成，準備進入 Stage 5。
- 2026-11-17：Stage 5 實作與驗證完成，準備進入 Stage 6。
- 2026-11-17：Stage 5 提交；Stage 6 完成 Worker env 白名單切片，程序控制仍待完成。
- 2026-11-17：Stage 6 完成 Windows／WSL Linux 程序故障驗證，準備進入 Stage 7。
- 2026-11-17：Stage 6 提交；Stage 7 讀取規格、盤點正式輸出與快取風險。
- 2026-11-17：Stage 7 完成契約、ArtifactSet 與 staging clone 切片；正式 promotion／rollback 尚待實作。
- 2026-11-17：Stage 7 版本交付、rollback、GC 與故障矩陣驗證完成，準備進入 Stage 8。
- 2026-11-17：Stage 7 提交；Stage 8 讀取 Error Catalog 與 CLI 規格。
- 2026-11-17：Stage 8 Error Catalog／診斷 CLI 完成驗證，準備進入 Stage 9。
- 2026-11-17：Stage 8 提交；Stage 9 盤點現有 plan/discovery/load 的持久化與程序副作用。


## [2026-11-17T04:45+08:00] Stage 9 Service 實作與回歸追查

<!-- devnote-review:efe90139af6dfc22315981b5 -->

完成 Service/CLI 主要委派、唯讀計畫與受限重試；安全重試衝突已修正，Stage 9 專項 13 passed，發行包建置成功。完整回歸仍多出失敗，待追查後才能提交 Stage 9。


## [2026-11-17T04:55+08:00] Stage 9 回歸恢復基線

<!-- devnote-review:25b9e9d2f2111cb6a6c5f016 -->

將 CLI/Service 對照測試置於獨立 Service client，並把插件快取修復測試改由 prepare 觸發，符合 plan 無副作用契約。全測 354 passed、15 個既有失敗、10 skipped；發行包含三個新 Core 模組。


## [2026-11-17T05:07+08:00] Stage 9 提交與 Stage 10 Windows Job 限制

<!-- devnote-review:faa46ef2050405cb5a147553 -->

Stage 9 提交 9ead2a4。Stage 10 的新版／legacy MCP 協商、工具列表、路徑拒絕與取消測試通過；Client 關閉／重連失敗，Windows SDK 會清理 Server 子程序樹。可選依賴沒有破壞一般 CLI 與打包。


## [2026-11-17T05:28+08:00] Stage 11 本機工作台完成驗證

<!-- devnote-review:bfc3f7f82aa9902c8f4ba1bf -->

建立 Tk 工作台與發行包 open_ui 入口；測試從 UI 按鈕完成範例並重開／搬移專案查舊成果。完整回歸除 Stage 10 未解測試外沒有新增失敗，POSIX ZIP 權限測試已修正。


## [2026-11-17T06:16+08:00] Stage 11 提交與跨平台整合驗收

<!-- devnote-review:659179117a844035887dbccb -->

Stage 11 提交 ad753e0。Stage 12 100 次混合 Run/Action、20 故障點、秘密遮罩與 5,000 Run 延遲取得證據；Windows 368 passed/1 failed/21 skipped，WSL Linux 378 passed/12 skipped。唯一 Windows 失敗仍是官方 MCP Client 關閉程序樹；Stage 13 文件草稿已標示未通過 gate。


## [2026-11-17T06:27+08:00] 跨平台回歸與 Windows 冷啟動修正

<!-- devnote-review:8d0ba1c753c9e704037e05cf -->

Windows 368 passed/1 MCP fail/21 skipped，WSL Linux 378 passed/12 skipped。乾淨 Windows ZIP 冷啟動揭露並修正 __version__ 與 wheel 不一致；bootstrap 改用 .NET SHA-256，冷啟動 list/run/doctor 通過。Stage12/13 文件與回歸草稿未完成，等待 Windows MCP 架構決定。


## [2026-11-17T06:41+08:00] Windows MCP 保留未完成的決定

<!-- devnote-review:490d43a69e684db2957a7556 -->

使用者明確選擇維持原規格、不新增獨立常駐服務；Stage10 Windows SDK Client 退出後原 Run 無法可靠續跑，MCP 專項 3 passed/1 failed。新增測試證明失聯 Run 在 lease 到期後可標為 interrupted 並保存事件，但不取代續跑需求。Stage12/13 草稿與 Gate 維持未完成。


## [2026-11-17T07:30+08:00] Stage 10 改為持久化佇列並完成本機驗證

<!-- devnote-review:ddadb3a34d453b3b76b4fbc5 -->

依最新使用者規劃修訂 Stage10：MCP 只入列與按 execution_id 查詢，獨立 queue-worker 執行，SQLite v004 建立 Run 前持久化 ID。Windows 全測 379 passed/21 skipped，WSL Linux 387 passed/12 skipped；乾淨發行包的 Client 關閉重連測試成功。Stage12/13 macOS與CI仍待。


## [2026-11-17T07:55+08:00] Stage12 本機整合矩陣通過

<!-- devnote-review:e6cf94e48fa759422a982144 -->

Stage12 新增 Action/Demo 失敗事件、佇列輸入漂移、v004 缺表與 100 次混合 Step 前後事件驗證；Windows 382 passed/21 skipped，WSL Linux 390 passed/13 skipped。C: NVMe 的 5,000 Run p95 9.78ms。正式 GitHub/macOS CI 未執行，Stage12 候選待提交與外部 Gate。


## [2026-11-17T08:01+08:00] Stage12 本機候選提交與發行包走查

<!-- devnote-review:a3cbda4e73ba5a53da111448 -->

Stage12 本機候選 a0495ba 已提交但正式 CI/macOS 仍待；Windows 382 passed/21 skipped，WSL Linux 390 passed/13 skipped。Stage13 文件 link 0 缺失，三 ZIP SHA 正確；乾淨 queue_worker.bat --once 在未裝 MCP 時啟動成功。GitHub 外部 CI 授權待使用者答覆。


## [2026-11-17T08:06+08:00] Stage13 文件候選與外部驗收待授權

<!-- devnote-review:4aaf8eeedfe4317ddd7163d4 -->

Stage13 文件候選 f69402f 已提交；本機三 ZIP SHA、source 文件、連結與 Worker 冷啟動通過。Windows 382 passed/21 skipped、WSL Linux 390 passed/13 skipped。正式 Ubuntu/Windows/macOS CI 與真人走查未完成，等待使用者對可能付費的 CI 推送/執行授權。


## [2026-11-17T08:35+08:00] 三平台 PR CI 通過，Stage13 人工 Gate 待驗

<!-- devnote-review:c76f4a3b22a97acd2afdc5ce -->

PR #3 第三輪 CI Ubuntu401/3skip、macOS402/2skip、Windows383/21skip 均成功；前兩輪揭露並修復 Windows cp1252 和 macOS 程序出生時間。Stage12 整合 Gate 已具三平台證據。Stage13 更新測試報告與真人走查步驟，仍待最後 doc-only CI、Mac GUI真人走查及發布授權。


## [2026-11-17T08:55+08:00] 最後文件 head 三平台 CI 通過

<!-- devnote-review:fbc79e0f03e7d22e5dc5c346 -->

PR #3 最終文件 head 0d2e48d 的 Actions run #11 Ubuntu401/3skip、macOS402/2skip、Windows383/21skip 全部 success，ZIP SHA正確且 publish skipped。使用者同意安排非技術走查，結果仍待回覆；PR保持 draft、未合併未發布。


## [2026-11-17T09:01+08:00] 舊0.2.9 wheel回滾可讀，雙擊入口限制

<!-- devnote-review:501661505046bc928a52a304 -->

找到本機舊發行包0.2.9；以 before-v001 還原後，PowerShell7執行未修改舊 start.ps1 可安裝 wheel 並讀 run_old。舊 run_app.bat 在此主機WinPS5.1因 Get-FileHash 缺失失敗。文件已區分兩者，等真人走查回報後再推最後 doc CI。


## [2026-11-17T09:06+08:00] 走查復原步驟補齊並重建發行包

<!-- devnote-review:d1b62609350f7e54ee1feaa4 -->

核對待整理 checkpoint 與工作樹後，在非技術走查第 5 步補上修正錯誤輸入、重新執行並比對成果的復原驗收；本機 release.py 建置成功，ZIP 中已有更新指引。真人走查未回報，文件仍未推 PR。


## [2026-11-17T09:24+08:00] PR 使用者合併與自動發行後的處置待決

<!-- devnote-review:27d7b4e6a53ec469ead3d997 -->

五份 Stage 13 文件以 264a605 推送，run #12 三平台成功。GitHub 事件顯示使用者帳號合併 PR #3；合併後 run #13 自動公開新 Release。使用者授權取消時 publish 已完成，未能取消；已請其決定是否刪除新 Release。伯東案例不上傳，當前倉庫與可見資產未發現匹配項；真人走查仍待回報。


## [2026-11-17T09:37+08:00] 手動發行 Gate 修正通過三平台 CI

<!-- devnote-review:3e947a741bde08a68fac3bf7 -->

新增 draft PR #5：以手動 main workflow_dispatch 加 publish_release 明確選項取代 PR 合併自動發布，仍以三平台測試為 publish 前置。run #14 三平台全成功、publish skipped；更新 Stage 13 文件的回滾與實際發行紀錄。已發布版本處置與真人走查仍待使用者。


## [2026-11-17T10:03+08:00] 保留已發布版本，修正文件通過三平台 CI

<!-- devnote-review:dc05c6f5bff9899687c4bc9e -->

使用者明確選擇保留 build-00fff1c。PR #5 文件記錄該決定，最新 head 2397ede 的 run #15 Ubuntu/Windows/macOS 全成功且 publish skipped；PR 保持 draft。線上 ZIP 下載逾時未得檔，不宣稱逐包內容檢查完成。Stage 13 真人走查仍待回報。


## [2026-11-17T10:51+08:00] PR #5 合併，main 採用手動發行 Gate

<!-- devnote-review:1c0332c00073837da523edc4 -->

使用者授權後將 PR #5 轉為 ready 並以預期 head SHA 合併；main 21fba443 已採用手動 publish_release 且保留三平台前置測試。合併後核對無新 Release，原 build-00fff1c 按使用者決定保留；Stage 13 真人走查仍待回報。


## 2026-11-17 Studio 新目標

使用者另行提供完整 Studio 規格並啟用 devloop；Stage 0 已提交 5543ebd，Stage 1 進行中。舊底座歷史與發布決定保留如上。

## 2026-11-17 Studio Stage 1–4 通過

持久化、來源安全預覽、IR v1 與工具目錄已各自提交；最新 ecf3e9a。89 項 Studio 測試通過，Stage 5 進行中。

## 2026-11-17 Studio Stage 5–7

串流 Discovery、資料夾證據與 Compiler 已提交 aade591、3621b8a、66427a1。124 tests passed，兩個生成案例經底座實跑，原有發行工具重建成功。Stage 8 進行中。


## [2026-11-18T00:59+08:00] Studio 工作台視覺缺陷修正與進度對齊

<!-- devnote-review:244a5fdd30341a49934540e0 -->

補齊 Stage 8–10 提交與 Stage 11 進行中狀態。使用者指出模糊後，已親自檢視原生 UI 並修復 DPI、字體、版面及操作狀態；最新 UI 針對性測試通過，使用者確認改善。完整規格、模型品質與平台 Gate 仍未完成。


## [2026-11-18T03:09+08:00] Studio Handler、明確驗收規則與累積修復

<!-- devnote-review:1a0b5cfa8df48a50439fbdd8 -->

核對並補齊前輪工作台與生成 Handler 提交；本輪完成 JSON 明確驗收、版本核准失效、案例與候選交易回復，以及第二輪接續前輪 IR 的修復。非桌面全測 230 passed／3 deselected，其後修復專項 4 passed；提交 6eb0ee6。建立完整需求缺口索引，Stage 12/13 與模型／真人／平台 Gate 仍未完成。


## [2026-11-18T03:23+08:00] 業務試做模型修復接線與隔離重播

<!-- devnote-review:c2f75f9beba52024d7f5f885 -->

提交 2a1505a：ModelRepair 具獨立資料授權、固定案例、兩輪串流及累積修復、Runtime 模型範圍限制與 UI 入口。CSV 算法和門檻經兩輪修復後真實重播通過。非桌面全測 246 passed／3 deselected；其後缺規則專項 1 passed。真實模型、平台與新 UI 視覺驗收未完成。


## [2026-11-18T03:35+08:00] 版本續編與案例歷史編輯

<!-- devnote-review:92512e35b6f00eb93767c5eb -->

提交 9331aaa：版本差異、歷史另存新稿、目標還原、草稿捨棄保護，以及案例歷史與版本化修改。非桌面全測 258 passed／3 deselected；後續核准競爭治理專項 15 passed。新視窗未實機驗收，完整編輯能力、模型品質、平台及交付 Gate 仍未完成。


## [2026-11-18T03:46+08:00] 原子流程編輯與連線工作表單

<!-- devnote-review:1c85166a83d19b63ccf22049 -->

提交 6033ded：FlowEditor、GraphDraft、節點新增／重用、來源／依賴／輸入格式／交付物及合併刪除。真實 Runtime 重播文字整理→指紋報告；專項 17 passed／2 deselected，全非桌面回歸 272 passed／3 deselected。新視窗未視覺驗收，完整政策與治理、真實模型、平台及交付仍待。


## [2026-11-18T04:05+08:00] 來源綁定人工證據決策與 v010

<!-- devnote-review:4bea53b17ba9a9ad71ec1f12 -->

提交 4abd425：規則說明與步驟引用、來源內容綁定的人工確認／撤回、跨流程驗證接線及發布前重檢；共用圖片預覽讀取歷史版本。v009 升級備份及還原通過。專項 28 passed，最終非桌面回歸 288 passed／3 deselected。機器證據、完整治理、新 UI 實機、模型品質及交付 Gate 仍待。


## [2026-11-18T04:30+08:00] 手動起案與型別化步驟設定

<!-- devnote-review:84cdb04339c98bad6cac933e -->

提交 e498a40：手動第一個流程、完整步驟設定表單、共同分支輸出型別、Runtime 模型模板及預算總額檢查。手動與兩路分支真實重播；非桌面全測 305 passed／3 deselected。原生表單互動、完整治理、模型品質、平台與交付 Gate 未完成。


## [2026-11-18T05:00+08:00] 工具治理、可用能力篩選與 v011

<!-- devnote-review:17df02b5f37ee378cee15eab -->

提交 ef488e3：工具描述與版本 UI、核准撤回及狀態事件、可編譯 available 與政策 allowed 分離、模型能力資訊與本機測試路徑排除。工具流程真實重播及停用 Gate 通過；非桌面全測 319 passed／3 deselected，後續模型專項 30 passed。v011 備份還原已驗；完整重用、治理、實機、模型品質與交付仍待。


## [2026-11-18T05:55+08:00] 固定 Handler 跨專案工具庫與 v012

<!-- devnote-review:e2678af914360ffaa7b929d6 -->

提交 053d062：已驗收生成步驟可保存為需核准的固定工具版本，同工作區跨專案重用且參數有型別限制。模型介面不攜固定程式／合成測試，實際新專案重播與參數修復通過。非桌面全測 332 passed／3 deselected；後續工具庫專項 14 passed。v012 備份還原已驗，完整治理、實機、模型品質與交付 Gate 仍待。


## [2026-11-18T07:01+08:00] 建置審核、完整案例與發布指標 Gate

<!-- devnote-review:9e1fc9722788e35cbee15618 -->

提交 a82676a：v013 審核與建置瀏覽、同修訂退回／重送、封存保護及明確來源版本更新。補完整案例集合與交付物 Gate、發布停用及理由，相同 manifest 決策指標與重複核准一致性，固定 ZIP 時間。專項 18 passed，最終非桌面回歸 349 passed／3 deselected。prepared 核對 UI、新視窗實機、模型品質、平台與交付 Gate 仍待。


## [2026-11-18T07:40+08:00] 發布中斷核對與 v014

<!-- devnote-review:59920780c23a77b4564ed0da -->

提交 3414be1：發布準備核對及不刪檔結束、已生效紀錄補齊、發布／目前版本共用鎖、停用中斷與過期／損毀／被取代保護。非桌面完整回歸 369 passed／3 deselected，最終鎖修正專項 38 passed；v013 備份／v014 升級／獨立還原與 UI model 重開通過。未對外發布或操作桌面；全目標及實機 Gate 仍未完成。


## [2026-11-18T07:53+08:00] 資料夾關係人工決策與候選採用

<!-- devnote-review:d1f15678ff696ce4f9a10560 -->

提交 c13cdb2：資料夾角色及關係決策、固定來源預覽、版本與理由，採用時重核來源及圖版本並原子保存確認／IR／案例。補已捨棄修訂的候選編號，舊流程不回寫。非桌面回歸 381 passed／3 deselected，最終專項 36 passed；新 UI 尚未實機驗收。全目標、機器證據、手繪、真實模型、三平台及最終交付仍待。


## [2026-11-18T08:08+08:00] 固定 Runtime 試做機器驗證證據

<!-- devnote-review:af8fb3c4c68e4e1f1e46613b -->

提交 b97030d：實際報告與產物可加入 verified 依據，原 ZIP／執行副本／案例／Runtime／實際成果皆重核。處理修改降級、人工機器標籤隔離，修復保留固定說明；新版本仍重測核准。最終非桌面回歸 400 passed／3 deselected；新 UI 未實機驗收。已確認一般 Discovery 不支援圖片，接續手繪與完整 Gate；全目標仍 active。


## [2026-11-18T08:28+08:00] 靜態流程圖片分析與傳送範圍預覽

<!-- devnote-review:613343c3dc8572da1cd7e7ae -->

提交 4c08767：圖片＋文字 Discovery、完整解析度／方向／metadata 處理、固定傳送預覽、圖片預留量、未知草稿與影片隔離。最終非桌面回歸 422 passed／3 deselected；真實辨識品質與新介面未驗。另確認背景完成會覆蓋未保存主目標文字，接續修復並保留 CAS 衝突語意。全目標與最終 Gate 仍 active。


## [2026-11-18T08:47+08:00] 背景目標草稿保護與外部版本比較

<!-- devnote-review:7b85e3a1ead25fc7b11bbf48 -->

提交 b3c207a：背景完成不覆蓋未儲存目標；獨立編輯基準、CAS 衝突與三方比較、跨專案保護、準備分析後重核、舊成功條件保留。非桌面回歸 435 passed／3 deselected，最終相關專項 20 passed／3 deselected。新 UI 未實機驗收；下一步回原規格核對核心 DoD 並推進正式矩陣，不能自創任意 IR 全支援 Gate，也不能用拒絕替代 P0/P1。全目標仍 active。


## [2026-11-18T09:10+08:00] Stage 12 本機基準與百次隔離

<!-- devnote-review:cf1e0f7e3e3700aa40a415aa -->

提交 42ef321：核心 DoD 八檔 37 passed，20 節點 p95 0.8214ms、100 來源零模型、100 建立／更新／重開、已發布包 100 次不同輸入實跑及交付重核通過。輸出槽重用的錯誤測試期待已改正，保留 OUTPUT_EXISTS 負例。硬體 SSD 與原始指紋報告已保存；Stage 12 主要進行中，20 故障／20 範例／三平台／真人與交付仍待。


## [2026-11-18T09:34+08:00] 二十發布故障點與程序中止驗收

<!-- devnote-review:d9f060b16f4a06c32fde5af3 -->

提交 49d1012：20 個唯一故障點 20 passed／91.19s，三個真正 os._exit 中止 3 passed／17.29s。每點保留舊發布快照與成果，核對提交邊界並實際重播舊包；JUnit 無 failure/error/skip。§12.1 五項 Windows 本機基準已具證據，不推論斷電／硬體或其他平台。Stage 12 仍進行中，20 範例／完整 AC／三平台／真人與最終交付待辦。


## [2026-11-18T11:28+08:00] 固定二十案例與來源缺陷修復

<!-- devnote-review:e32c25480dcc54c696d41ca1 -->

提交 ea69748：acceptance-v1 20 件／21 訓練／40 未見，結果維持 not_run；61 組預期與來源隔離核對，五個資料夾真正重播／隔離發布。修復多選只送首項、預覽焦點與空檔預期匯入，157 項相關回歸通過；155 個 Git index 檔案 bytes 與指紋相符。未把工程測試冒充真人或真實模型品質；完整 Stage 12/13 仍未完成。


## [2026-11-18T13:41+08:00] 字幕 CUDA 環境準備序列化修正

<!-- devnote-review:09ef94fa7ec55d39a5c6be0e -->

對照 D 槽安裝版與主線修正，重現 current 名稱遮蔽，恢復位置紀錄寫入並通過 39 個測試。Studio 分支進展依 Git 核對，外部 CI 狀態未於本輪查證。


## [2026-11-18T13:43+08:00] Studio後續開發移交隔離工作樹與PR9

<!-- devnote-review:28ec41496184fe4270a06fb0 -->

整理 checkpoint 1f5e456a45280238ca2039ac：安全與續編測試已提交；保留原 E 使用者變更，在 C 管理工作樹整合main並建立草稿PR9。CI揭露POSIX venv解析與Windows CRLF測試，3924c6d已修；最新run的macOS/Linux Studio成功，Windows待結果。後續以C工作樹交接為準。 與13:41字幕CUDA環境準備更新調和，保留其決策、限制與驗證紀錄。 原本1f5e checkpoint已由並行工作處理；本次不確認其他工作的新checkpoint。


## [2026-11-18T13:48+08:00] 核對字幕底座實機修復紀錄

<!-- devnote-review:a6c9fb020ccde0fc8c49aa61 -->

核對待整理的前輪字幕修復紀錄、發行包雜湊與 D 槽環境位置。實際模型推論未驗；Studio 工作仍沿用獨立工作樹交接。


## [2026-11-19T22:30+08:00] 底座 0.2.13 宿主 ZIP 附件更新

<!-- devnote-review:b07a1ea73c8083c402b5e116 -->

完成已安裝流程的 Service／CLI／MCP ZIP 更新、備份回退與中斷復原；保留設定、成果與歷史。專項 35 passed；非桌面回歸 418 passed／21 skipped／1 deselected，打包缺件補齊後單項 1 passed；乾淨 wheel 安裝 smoke 通過。新增檔 Ruff 通過，6 個既有 lint 未動。產出本機 0.2.13 ZIP，未對外發布、未操作既有使用者安裝；Studio 與原 dirty 修改保留。


## [2026-11-20T00:50+08:00] 底座 0.2.14 訪談藍圖首次安裝

<!-- devnote-review:9bdfde166eb3f5e8d6c051c4 -->

完成目標宿主的草稿、候選程式、驗收、原子首次安裝與跨對話待辦。實際 Reel 藍圖用合成媒體通過平台回執與人工交付兩種試做；正式帳號未驗。Windows 非桌面回歸 432 passed、21 skipped、1 deselected，最終專項 16 passed。


## [2026-11-20T12:27+08:00] D 工作區更新與 Reel 僅安裝

<!-- devnote-review:71d53fd000eeb24e6650b508 -->

更新 D:/RoutineKeel 至基座 0.2.14，保留 15 筆歷史與 21645 個流程檔。將指定人工上傳 Reel 附件轉換為 0.2.2 並安裝，綁定 Akool；本機兩案零 provider calls 通過，未啟動正式流程。使用者只安裝指示前有一筆 1-credit 語音及兩張合成圖，其後停止新增生成。外部能力的唯讀探測與未執行功能驗收分列；舊 Studio 工作保持原交接。


## [2026-11-21T03:20+08:00] 重建本機 Release 更新目錄與校正 Studio GitHub 狀態

<!-- devnote-review:1fe338ff9e3022bc874f237e -->

全元件建置產生底座0.2.14／複製0.2.2／字幕0.3.4與一致校驗清單，舊 release 已備份。封裝測試1 passed，巢狀封裝、SHA256與workspace核對PASS。未發布或呼叫付費流程。GitHub查核修正過期交接：PR9已合併，Studio位於main的routinekeel/workflow_studio/；預設三ZIP不包含Studio。


## [2026-11-21T22:22+08:00] 統一來源與發行結構，採 E 底座、字幕 0.5.0 與對話 Studio

<!-- devnote-review:31c008ec83cdfd64a262168a -->

依使用者選擇整合 E 底座與 GitHub 字幕 0.5.0，將原始碼搬至與安裝相同的根層結構。排除 Studio Tk 並保留無視窗核心，新增 guide 入口及驗證。更新／回退支援巢狀流程與技能附件，五 ZIP 已建置；529／515／90 項測試通過。舊來源、SQLite、Tk 與 E staging 有備份與核對；D 僅唯讀檢查，未執行付費生成或發布。


## [2026-11-21T22:45+08:00] 修正 Windows checkout 的模板換行驗證

<!-- devnote-review:7de286b9c6e1e5f0c6388108 -->

PR #13 首輪 CI 僅 Windows Studio 失敗：模板為 CRLF 時測試再次替換 LF，生成 CRCRLF。固定模板 checkout 為 LF，改以 LF／CRLF 兩種來源做舊套件實際執行測試，並確認異常 CRCRLF 仍拒絕。專項 5 passed、lint／format 通過；未放寬模板信任邏輯，等待新提交三平台 CI。


## [2026-11-21T22:48+08:00] 整合遠端修正並保持模板信任邊界

<!-- devnote-review:4c9eafee521c5fdafd1c116f -->

遠端出現 Copilot 5a9febd，先 fetch 並 merge 保留歷史，不強推。實測其換行正規化會接受改變字串內容的模板，維持原有嚴格規則並用新增反例保護。模板與安全專項 9 passed；等待新 head 三平台 CI。


## [2026-11-22T13:34+08:00] 完成 main 正式 Release 發布與 Latest 附件核對

<!-- devnote-review:47c95b87edb27ae738cb82ad -->

依使用者明確更新 RELEASE 的要求，PR #13 合併至 426ba934，經既有手動 Gate 啟動 run 44917893261。七個工作全部成功；Latest 更新為 build-426ba934...。在 GitHub UI 核對五 ZIP 及校驗檔，資產 SHA 與正式 CI artifact 相同，C/E release/workspace/wheels 同步正式產物。保留舊版、備份及 E staging，未更新 D 或執行付費生成。CLI 憑證轉用被拒後已停止，改用正常登入 UI 完成發行。


## [2026-11-22T15:34+08:00] RK-NEXT Stage 0 可重現基線與回復驗證

<!-- devnote-review:9313b4348683da52ee8ed22e -->

核對指定規格、原發行與工作樹，通過 Windows 全套及平台／音訊補測，建立下一階段依據。


## [2026-11-22T15:43+08:00] RK-NEXT Stage 1 共用必要驗證器

<!-- devnote-review:4080d30cdb512f971657f6fd -->

試做與安裝重新檢查 IR、工具及 wheel RECORD，實測驗證器缺失與例外皆阻擋，舊流程依原入口運作。


## [2026-11-22T15:51+08:00] RK-NEXT Stage 2 圖形契約與來源相容性

<!-- devnote-review:64cb287676a5c0d4c9af967b -->

亂序 DAG 測試發現控制邊曾影響重複依賴判斷，已修正並重跑。Graph 層使用共用契約，正式執行仍待後續 Builder 與 Scheduler。


## [2026-11-22T15:57+08:00] RK-NEXT Stage 3 持久化訪談與復原

<!-- devnote-review:9fbe34961b660837a8e741f4 -->

原專案 API 與訪談共享版本並在同一交易同步目標。測試跨程序重開、同版並發、重送、封存、上游修訂及 v14 回復。


## [2026-11-22T16:21+08:00] RK-NEXT Stage 4 對話介接與終端信任條件

<!-- devnote-review:f12a1f99e1e175637cccb4fc -->

依使用者決定，宿主負責提問與呈現，可信本機文字 CLI 確認。Stage 4 的 CLI、MCP 與候選包專項已過；首次底座全測有診斷碼與監督程序測試失敗，已修復並回歸。


## [2026-11-22T16:41+08:00] RK-NEXT Stage 4 桌面對話與關鍵點校閱

<!-- devnote-review:5a8a7792c1663ed418f1c951 -->

依使用者決定，採信桌面宿主轉交的實際回覆。已修正診斷碼、監督程序測試及舊 v1 最大流程轉換；開發回歸並未改成人工逐步核准。


## [2026-11-22T16:48+08:00] RK-NEXT Stage 5 不可執行藍圖匯出

<!-- devnote-review:6103aaa7e914f5277d283a49 -->

設計預覽綁定專案版本、具名能力與所選技能指紋。拒絕或過期不匯出，重開專案可重取同一不可變藍圖。


## [2026-11-22T16:59+08:00] RK-NEXT Stage 6 能力綁定與宿主工具清冊

<!-- devnote-review:8449d660f5a967c5d74a1cd3 -->

新增能力解析入口，從連接中的 MCP client 實際讀取清單。驗證版本、權限、離線與宿主回執；只有缺口才要求處理。


## [2026-11-22T17:12+08:00] RK-NEXT Stage 7 Graph Builder 與共用核發政策

<!-- devnote-review:224b862db04fb8d0caa4c0dc -->

桌面對話設計匯出、能力解析到真實試做及安裝串接。舊 Studio 相容後端也須經共用收據；已啟用訪談的專案不能從舊發布 API 啟用新版本。


## [2026-11-22T17:25+08:00] RK-NEXT Stage 8 依節點恢復與回修

<!-- devnote-review:5863965e78e04c50c7d1c52c -->

新增分支兄弟保留、程序中斷、具名多待辦與取消重試測試。釐清臨時工作區載入的是候選工作流，並非原始藍圖。


## [2026-11-22T17:34+08:00] RK-NEXT Stage 9 選路、跳過與具名匯合

<!-- devnote-review:7985270fd5e658f763d62548 -->

雙路徑由真正候選工作流重播；補上有型別的條件欄位、明確備援及決策回修。


## [2026-11-22T18:05+08:00] RK-NEXT Stage 10 對話核准及對外回執

<!-- devnote-review:4bc7cb841f9fa0f3987cf464 -->

同一待辦重複領取不重新派送，拒絕、過期版本與 actor 自述不能授權。修正隔離規畫程序無法取得非敏感能力描述的問題。


## [2026-11-22T18:31+08:00] RK-NEXT Stage 11 有界子圖及迭代恢復

<!-- devnote-review:8a3e59894daf1cb07ddcf175 -->

空集合、上限、零次／一次重做、故障後續編、部分重試、取消、模型回執與預算均有實測。未呼叫付費模型。


## [2026-11-22T19:08+08:00] RK-NEXT Stage 12 品質評估、預覽與正式交付

<!-- devnote-review:a23c1e09734a175778ff1370 -->

未驗收成果另存不可變預覽。修正另建版本，重驗後可正式交付。測試實際還原 v4 備份。MCP 重連測試改為先結束提交者，再啟動獨立 Worker，避免空閒逾時先退出。


## [2026-11-22T20:05+08:00] RK-NEXT Stage 13 修訂比較與可恢復升版

<!-- devnote-review:7e558cf8460ddaeaa61bf367 -->

第一輪底座回歸遇到模組搬移時的三例匯入失敗及一例 Worker 冷啟計時失敗。修復後整組 83 項重跑皆通過；另實測切換中斷、重試及回復後執行舊版。


## [2026-11-22T20:25+08:00] RK-NEXT Stage 14 Agent 目錄與對話續接

<!-- devnote-review:37acb3ed919f9b4631c29701 -->

真實 MCP 提交後斷線並重連，區分完成的 Run、仍等待的人工節點與品質驗收。修正取消測例使用已不存在的假 PID；完整 22 項連同既有 Service 回歸通過。


## [2026-11-22T21:05+08:00] RK-NEXT Stage 15 全庫清理與相容性修正

<!-- devnote-review:3fc41c1246c7617ca39c50dd -->

已獲明確授權清理既有診斷。353 個受掃描檔案通過全庫 Ruff，Studio 原規則及 164 個檔案 format 通過。初次全測發現字幕 resource_path 公開轉出遭移除，已明確恢復。清理前的 Windows 兩組測試已停止，會重新完整驗收。


## [2026-11-22T23:56+08:00] RK-NEXT Stage 15 本機技術驗收

<!-- devnote-review:a92a35a5c6dca3791187b513 -->

Windows 及 WSL 實測逐一核對 1,190 個唯一測例，補驗逾時與所有平台／依賴條件。修復冷啟動子程序被過早判定中斷的空窗。實際解壓冷啟動，包內 MCP 列出 32 個工具並續編既有訪談。


## [2026-11-23T00:28+08:00] RK-NEXT Stage 16 最終 0.3.0 本機候選

<!-- devnote-review:b9fd72cda8f0e829edc078bc -->

獨立建立 RC1，未覆蓋既有發行目錄。五 ZIP 與六 wheel 校驗，解壓後建新環境、32 個 MCP 工具、提交／重連／成果及實際 0.2.3 → 0.2.4 → 0.2.3 回復通過。Stage 16 回歸 106+31 全過，完整收集逐案 659/659 與 534/534。文件與打包後本機連結均有效。


## [2026-11-23T01:00+08:00] 按開發時序與主題提交 RK-NEXT 0.3.0 RC1

<!-- devnote-review:149dc347e10aaa25a281f0a0 -->

以 ad8dbcb9f141 為原分支起點，保留 a14b9afd7ab2 的工作區重整歷史，按已保存的階段原始碼、功能依賴與驗收文件分組，共 13 筆整合與實作提交，交接另立一筆。比對入庫原始碼與工作樹、重驗 Ruff 及 3 項候選交付測試。7 個原有暫存 IDE 檔案另留暫存區。


## [2026-11-23T02:10+08:00] Studio 批次訪談與字幕藍圖預檢

<!-- devnote-review:a476a6927c9dd479d6c642cd -->

從既有 E:/RoutineKeel 安裝取得原始 ZIP 與對話證據。修復宿主節點參數支援與批次訪談，補建置預檢及可執行案例檢查。候選可匯入原 ZIP 並清楚診斷剩餘缺口。原設計聊天 7 輪合計約 16 分 53 秒，68 次命令合計約 1 分 37 秒。


## [2026-11-23T07:03+08:00] v1.3.0 資產契約與 Registry

<!-- devnote-review:520898f32a0e4a0da3e30feb -->

核對規格與合併基線，完成 Stage 17 可攜資產契約及 Stage 18 Registry。修復 Windows NUL 的核准入口辨識後重跑，實測停用後的舊工作流與 Studio v15 遷移還原。


## [2026-11-23T08:27+08:00] 完成 v1.3.0 Stage 17–26 本機候選與驗收

<!-- devnote-review:c4db6082762b6ce79029696f -->

依規格完成擷取、資格、推薦、合成、環境鎖與安全演進。Windows、WSL 與音訊選配環境逐案補測。冷啟動最終候選並重播跨環境流程，八個發行資產指紋核對一致。macOS 與正式發行待另行授權。


## [2026-11-23T10:00+08:00] 實作 Studio 初稿、定稿與執行檢核

<!-- devnote-review:4c70ac8d63f32fe574b62142 -->

核對使用者補充規範與前一輪工作指引，接上 Studio、CLI、MCP、改版及底座檢核。定稿綁定全部顯示內容，人審與兩種錯誤處理通過回歸。


## [2026-11-23T11:20+08:00] 修復 Studio 稽核並保留空白底座

<!-- devnote-review:21d4750d4022f9aeaf95990f -->

核對已確認藍圖、成果更正、Pack 停用及 Pattern 改版的執行閘門。依使用者簡化要求，整合包的兩個參考流程收進 examples/workflows 下的現有 ZIP。已確認空白 catalog 與單獨啟用範例。


## [2026-11-23T11:32+08:00] 按主題提交 Studio 稽核與整合包調整

<!-- devnote-review:99b992b521885deced8e3ae1 -->

將已驗證的修正拆為 Studio 稽核與空白底座發行布局兩項提交。保留既有測試紀錄。


## [2026-11-24T21:03+08:00] 藍圖補強 Stage 0 基線

<!-- devnote-review:4a11d23a31bceba307a7783e -->

實測預檢同時有能力與案例缺口，仍可完成定稿、匯出與匯入草稿。關聯回歸 47 項通過。


## [2026-11-24T21:11+08:00] 藍圖補強 Stage 1–2 續做與自動資格驗證

<!-- devnote-review:1be1d7c92615c0a101797eb5 -->

加上可恢復的補強資料與 CLI／MCP 入口，接上安全本機資格驗證及環境鎖定。Stage 1 關聯 24 項、Stage 2 關聯 46 項均通過。


## [2026-11-24T21:46+08:00] 藍圖補強 Stage 3–4 補件、隔離試做與正式交付

<!-- devnote-review:6001ccb198b0f7ab8209d0d3 -->

完成可執行案例、素材、Studio 修訂與底座候選試做接續。驗證首次安裝、工作程序恢復及既有工作流的回歸比較升級。Stage 5 已補 Linux、字幕依賴及媒體實測，固定快照和冷啟動仍在驗收。


## [2026-11-24T21:57+08:00] 底座通用步驟與 Documa 整合

<!-- devnote-review:a4ccaea6bf786beb9e57157e -->

Studio 定稿加入版本化底座步驟和資料埠。底座將輸出品質檢核與交付組裝拆開，加入規則勾稽、文件索引、圖技能載入和批評修訂通路。Windows 專項、MCP、交付及兩環境 wheel 驗證通過。正式模型供應商呼叫保留待宿主驗證。


## [2026-11-24T22:01+08:00] 補記 Studio 全量與定稿重跑

<!-- devnote-review:102d40a679e8d6928bc7f99c -->

全量首輪跨程序使用不同的開發中契約，兩個 Pattern 定稿匯入被擋。固定最終程式後兩項均重跑通過。


## [2026-11-24T22:06+08:00] 藍圖補強 Stage 5–6 回歸與本機候選

<!-- devnote-review:11e633695762acfb5492289e -->

逐案例核對 1,299 個通過證據。從解壓候選實跑 Studio 匯出、MCP 續接、資格鎖定、試做、安裝及正式執行，核對最終候選與兩份操作指南。


## [2026-11-24T22:55+08:00] 底座通用步驟 Stage 0–2

<!-- devnote-review:a6f12fc67399b36c4e68158e -->

完成基線、定稿至正式交付及勾稽驗收。具名成果暫存清理有新測例，既有正式交付保留。


## [2026-11-24T22:58+08:00] 底座通用步驟 Stage 3–4

<!-- devnote-review:0efa6715fd99a3a3bd2bfbe6 -->

核對 Documa 索引重建及缺少執行檔分支，並修復格式錯誤的模型回執無法正常結束的情形。


## [2026-11-24T23:22+08:00] 底座通用步驟 Stage 5–6 結案

<!-- devnote-review:b3a87fd8d0a5213ee74a0e44 -->

補齊中文診斷並重建本機候選。固定快照回歸、平台補測、兩套隔離 wheel 真實交付及文件檢查均通過。
