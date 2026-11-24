# 規格整理 v 1.2.0

## Codex / Claude Code 分階段開發計畫

**RoutineKeel 通用工具鏈補全** · 2026-11-25

[技術規格與 A1–A11](technical.md) · [白話規格](user.md)

從儲存庫根目錄逐階段貼用對應宿主的指令區塊。每階段先核對實際差異、相容契約及回滾點。回報變更、測試命令、數量、失敗證據、未測範圍和可重跑產物。完成當階段 DoD 後進下一階段。跨平台、付費、外送及正式發布依使用者授權取得實際證據。功能驗收以安裝後實跑為準，先前底座測試只作為回歸基線。

### Stage 0 固定基線與可重跑路徑

前置為目前 `main`，核對 `c9c92b8` 的工具鏈盤點。交付既有 v1／v2、Pattern、CLI／MCP、安裝及 Schema 回復的基線表。固定領域測具、搜尋政策、計時環境與失效事件，重現「沒有單一合格工具」及已就緒的綁定。驗證工具沿用 `flow_base` 與 `workflow_studio` 的 uv、pytest、Ruff 和現有 CI 設定。以隔離測具保存環境配置與可還原資料，將命令納入基線文件。此階段回退可移除新增的驗收資料和基線文件差異。

**Codex Instructions**

```text
在 RoutineKeel 根目錄執行 Stage 0。讀 AGENTS.md 與 docs/specs/toolchain-completion/technical.md。
核對 graph/portable.py、qualification.py、blueprints/completion.py、installer.py 及 tests/e2e。明確記錄兩套隔離環境、已安裝包與資料回復基線。驗證 lint、測試工具和環境設定範本。以測具固定缺單一工具與既有綁定兩路徑，重跑基線。
交付檔案清單、命令、通過與未測項、資料備份還原方式。DoD 為驗收路徑可重跑並可區分既有失敗。
```

**Claude Code Instructions**

```text
在 RoutineKeel 根目錄執行 Stage 0。讀適用的 AGENTS.md 和 docs/specs/toolchain-completion/technical.md。
檢查 flow_base 的 Graph、版本鎖定與安裝入口，核對 tests/、tests/studio/、tests/e2e/ 的現有測試。建立不依正式服務的測具，覆蓋缺單一工具、正常候選和舊版資料。固定 lint、啟動與隔離環境命令。
提交基線文件、實測結果及還原步驟。DoD 為後續各階段可以用相同輸入重跑。
```

### Stage 1 打通通用需求與候選診斷

前置 Stage 0。交付一般節點與 Pattern 共用的需求觀測格式及待辦。測試範圍為 A1、A4 的診斷與相容性。回退時停用新需求入口，保留快照和既有解析入口。若涉及 SQLite 新版本，回退前保存新版資料再用已驗證的舊版備份還原。

**Codex Instructions**

```text
實作 Stage 1。讀 AGENTS.md 與 docs/specs/toolchain-completion/technical.md 第 1 節。
修改 flow_base/components/workflow_contracts、graph/portable.py、graph/capabilities.py、adapters/host_inventory.py 與 blueprint_completion。加入節點抽象需求、觀測快照及逐候選淘汰理由。保持 v1、舊 v2、Pattern 及原 CLI/MCP 行為。
用 tests/ 及 tests/studio/ 驗證一般節點、巢狀節點、清冊過期、權限及型別缺口。交付相容性測試和缺口回傳範例。DoD 為缺口可在同一工作續查。
```

**Claude Code Instructions**

```text
按 docs/specs/toolchain-completion/technical.md 第 1 節實作 Stage 1。沿共用契約、graph/portable.py、graph/capabilities.py、adapters/host_inventory.py 與既有補強入口擴充。
在測試覆蓋穩定節點路徑、Pattern 來源、已安裝與有權使用的差別，及逐項淘汰。實跑舊入口回歸，整理必要資料備份及對照。
交付欄位、測試及回滾指引。DoD 為同一工作能查明原因、刷新觀測及續做。
```

### Stage 2 規劃並編譯可試做的多工具方案

前置 Stage 1。使用已可呼叫的測具完成 A2 的多工具規劃。失效或錯誤候選保留證據。回退時停用新規劃入口，保持既有單工具綁定和已保存候選。

**Codex Instructions**

```text
實作 Stage 2。從 docs/specs/toolchain-completion/technical.md 第 2 節落地 graph 規劃與編譯，重用 builder.py、qualification.py 及共用契約。
驗證型別與語意、串接、匯合、全鏈預算、無候選和搜尋上限。保存原節點到展開圖及淘汰原因。底座重驗宿主方案，涉及模型的可見文字必須 Streaming。
交付可重現的 A2 規劃案例與拓撲證據。DoD 為無單一合格工具時可編譯出符合契約的子圖並實跑。
```

**Claude Code Instructions**

```text
按 docs/specs/toolchain-completion/technical.md 第 2 節實作 Stage 2。修改 graph 規劃與 Builder 編譯，以及 workflow_contracts 的工具鏈契約。
用三個分離工具與不合規候選驗證串接、匯合、資料目的地、全鏈成本、穩定排序與限額續行。模型對話輸出必須 Streaming。
交付方案映射、實跑證據與回滾開關。DoD 為底座產生的完整子圖可用獨立預期值通過案例。
```

### Stage 3 實際補足環境與宿主配置

前置 Stage 2。交付受信配方、隔離準備、重新觀測、具名回執及補償動作。完成 A2 的套件補全、A4／A5 的等待、併發與中斷，以及 A10 的宿主設定。回退時停止配方執行。保留其隔離候選與具名回執，依已有授權執行可驗證的補償。

**Codex Instructions**

```text
實作 Stage 3。按 docs/specs/toolchain-completion/technical.md 第 3 節擴充 blueprints/completion、completion_store、adapters、Pack 契約與隔離執行程序。
先持久記錄意圖及授權，再實裝鎖定依賴或依具名宿主配方配置。重新觀測，保存回執，涵蓋失敗、補償及回執待查。
跑 A2、A4、A5、A10 的配方測試，交付套件指紋、操作證據與回退紀錄。DoD 為起初缺套件的工具鏈自動準備完成，且宿主配置中斷可查證原操作。
```

**Claude Code Instructions**

```text
按 docs/specs/toolchain-completion/technical.md 第 3 節實作 Stage 3。工作限於底座補強、配方契約、受信 Adapter 準備和宿主回執。
實測 A2、A4、A5、A10 的隔離安裝、版本漂移、授權失效及中斷復原。在隔離服務核對已配置回執待查，並驗證補償及重送去重。
交付程序事件、狀態和測試。DoD 為依賴和配置項各有實際結果，失敗可在原工作恢復。
```

### Stage 4 建立 Adapter 並完成整鏈資格

前置 Stage 3。交付可登錄 Adapter 產物、獨立案例與正式候選重驗。涵蓋 A3 的具型別映射、A11 的程式 Adapter，以及 A7 的驗證邊界。回退時停用相應建置來源或封存新 Adapter，保留已安裝可用版本及證據。

**Codex Instructions**

```text
實作 Stage 4。按 docs/specs/toolchain-completion/technical.md 第 3、4 節修改 Adapter 編譯與隔離建置、graph/qualification.py、Builder 和 installer.py。
把受支援的映射和轉換實際建成產物。通用程式候選須有來源、隔離證據及資格。保存原驗收意圖與獨立預期值。模型輸出的可見文字必須 Streaming。
驗證 A3、A7、A11 的建置、行為、整鏈與啟用前重驗。交付兩種 Adapter 的實際候選、收據及回滾紀錄。DoD 為必要驗證均通過的候選可安裝。
```

**Claude Code Instructions**

```text
按 docs/specs/toolchain-completion/technical.md 第 3、4 節實作 Stage 4。處理 Adapter 產物生命週期，以及共用驗證、Builder、試做及安裝證據。
以具型別映射和受控程式來源各建立一個實際 Adapter。驗證隔離不可用、試做失敗、改動預期值、失真轉換及套件竄改均有具體處理結果。模型輸出必須 Streaming。
交付可重跑的分層觀測。DoD 為 A3、A11 產物能登錄、整鏈可試做、A7 的必要關卡有效。
```

### Stage 5 接上失效偵測、重綁和續做

前置 Stage 4。交付 Run 失效事件、重綁工作、新啟用修訂、續做與回退。涵蓋 A6、A7。回退停用自動觸發，依收據原子回到已驗證前版並保留新舊 Run。

**Codex Instructions**

```text
實作 Stage 5。按 docs/specs/toolchain-completion/technical.md 第 5 節修改 Runtime、補強工作、環境鎖與正式啟用。
用失效證據建立去重的重綁工作，走同一規劃、補全、資格與核准。換綁由同一工作原子建立唯一續做 Run，記錄 parent_run_id，保留可重用成果及外部回執。
驗證 A6、A7 的原工具恢復、重送、未知回執及三處交接中斷。檢查同一工作 ID、相關 Run、產物及實際派送次數。DoD 為換綁安全續做、原 Run 歷史可查且可回退。
```

**Claude Code Instructions**

```text
按照 docs/specs/toolchain-completion/technical.md 第 5 節實作 Stage 5。範圍為 Runtime 偵測、補強續接、版本啟用及回退。
測試原綁定重驗後續跑原 Run，以及換綁後用同一工作 ID 查到唯一續做 Run。驗證受影響後代、parent_run_id、安全成果及三個交接中斷點。外部回執不明依原操作 ID 查核，具業務或授權差異走新核准。
交付 A6、A7 的狀態、收據與版本回退證據。DoD 為實際失效能自動建立補強工作並安全接續，API 可查詢原 Run 與續做成果。
```

### Stage 6 補齊整合、回歸與發行驗收

前置 Stage 5。驗收 A1–A11，同一工作跑完工具鏈補全與失效重綁。至少 Windows／Linux、現有 CLI、真實 MCP stdio、Studio 與舊版流程。回退時按失敗的切片停用或還原，保留測例和隔離服務的回執。

**Codex Instructions**

```text
在 tests/、tests/studio/、tests/e2e/ 補齊 docs/specs/toolchain-completion/technical.md A1–A11。由 tools/release.py 建置具名本機候選。
實測多工具、實裝依賴、建置 Adapter、外部回執待查、移機、重啟、原子啟用及回退。驗證 Windows 和 Linux 的隔離環境、v1/v2 相容、真實 MCP 及資料庫還原。
交付測試數、失敗、未測平台與實際驗收收據。DoD 為必要關卡全通過，A9 從原需求到兩次綁定均可追查。
```

**Claude Code Instructions**

```text
按 docs/specs/toolchain-completion/technical.md A1–A11 執行 Stage 6。測試目錄為 tests/、tests/studio/、tests/e2e/。使用 tools/release.py 實測封裝後的兩個隔離環境及工作程序。
核對兩個領域的多工具候選、產物鏈、同一工作失效重綁及原收據，重跑 CLI、MCP、Studio、舊流程與備份回復。
交付可重跑命令、實際驗收與逐項限制。DoD 為完整放行矩陣有證據。
```

### Stage 7 完成文件與本機交付

前置 Stage 6 通過。交付操作、授權、診斷、維護、部署與回滾指引。使用已驗證的本機候選、驗收紀錄及未測範圍交接。文件訂正可回到相符的產品版本。

**Codex Instructions**

```text
依 Stage 6 證據與 docs/specs/toolchain-completion/technical.md 完成 Stage 7。更新 flow_base/docs/blueprint-install.md、workflow_studio/docs/host-conversation.md、適用的 AGENTS.md 及 DEVNOTE.md。
使用實際輸出說明候選比較、受控補全、失效重綁與回退。重跑文件命令，核對封裝內文件、核准要求及限制。
交付版本化候選與證據索引。DoD 為操作者可依指南完成、續做或恢復同一工作。
```

**Claude Code Instructions**

```text
依 Stage 6 的實測與 docs/specs/toolchain-completion/technical.md 完成 Stage 7。更新底座安裝與回退指南、Studio 宿主文件、適用 AGENTS.md 及 DEVNOTE.md。
以實際案例展示工具鏈補全、授權、驗證、重綁及恢復。逐條核對命令、包內文件、版本與未測範圍。
交付本機候選、維護手冊與回復證據。DoD 為文件和封裝行為相符、證據可重跑。
```

### 規格檢查紀錄

2026-11-25 以 `check_plain_language.py` 檢查白話版，通過禁語檢查。以 Python `pathlib`、`re` 及 `xml.etree.ElementTree` 檢查三份文件標題、23 個本機連結、兩張 SVG 結構與可及標籤、A1–A11 編號及 Stage 0–7 的 16 個貼用區塊，均通過。`git diff --check` 通過，另檢查未追蹤文件與 SVG 的行尾空白。功能與候選封包的執行證據由 Stage 0–7 產生。
