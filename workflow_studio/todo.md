# Workflow Compiler & Studio 階段清單

## 底座通用步驟與 Documa

依[分階段開發計畫](../docs/specs/base-steps/implementation-plan.md)及[實測紀錄](../docs/specs/base-steps/verification.md)驗收。Stage 編號屬此項變更。

- [x] Stage 0 建立可重跑的基線。
- [x] Stage 1 打通定稿到正式成果。
- [x] Stage 2 驗證報表勾稽與計畫重用。
- [x] Stage 3 驗證 Documa 文件與技能入口。
- [x] Stage 4 驗證模型審查與有限回修。
- [x] Stage 5 補齊整合、回歸與邊界測試。
- [x] Stage 6 完成文件與交接。

## 現行宿主互動工作

- 已移除舊網頁流程、MCP 引導入口與專屬測試，保留通用服務、既有 Tk 及資料庫相容性。
- 新方向以 [宿主對話逐層確認規格](specs/redesign-host-reasoning.md) 為準。
- [ ] 設計並實作逐層狀態、問題／答案契約及確認紀錄。
- [ ] 接入 Claude Code 與 Codex，分別完成真實宿主驗收。
- [ ] 實作藍圖包匯出與完整性檢查。
- [ ] 依後續決策設計轉換器與底座首次安裝。

## 既有套件階段紀錄

以下保留原套件的歷史進度，不代表新的宿主流程已完成。

RK-NEXT v1.3.0 的 [Stage 17 資產契約](../docs/development/stage-17-report.md)及
[Stage 18 Registry](../docs/development/stage-18-report.md)已通過 Windows 專項。
[Stage 19 子圖擷取](../docs/development/stage-19-report.md)已由已安裝工作流實際驗證。
[Stage 20 獨立試做](../docs/development/stage-20-report.md)已完成 Windows 專項。
[Stage 21 設計期推薦](../docs/development/stage-21-report.md)通過 40 組固定案例。
[Stage 22 圖形合成](../docs/development/stage-22-report.md)已通過既有 Builder 與品質 Gate。
[Stage 23 能力解析](../docs/development/stage-23-report.md)已驗證本機替換仍需資格。
[Stage 24 資格鎖定](../docs/development/stage-24-report.md)已實跑本機安裝與宿主斷線 fixture。
[Stage 25 影響與演進](../docs/development/stage-25-report.md)已重播兩個受影響工作流。
[Stage 26 跨環境與封裝](../docs/development/stage-26-report.md)已完成本機候選與回歸。正式發布及 macOS 實測另依驗收範圍。

RK-NEXT Stage 1 已將 IR、參照、埠合約與 wheel 驗證抽到共用套件。原 Studio 匯入名稱保留，底座與 Studio 的安裝環境隔離。驗收見根目錄 `docs/specs/rk-next-baseline.md`。

RK-NEXT Stage 2 的 Graph v2 格式與具名交付已完成靜態驗證。Stage 3 以 Studio schema 15 保存同一專案的訪談快照、來源、確認與失效事件。跨程序重開、並發衝突及 v14 備份還原均有測試。對話宿主介接由 Stage 4 負責。

目前交付以 `docs/host-conversation.md` 為準：對話內引導、`guide`／`projects`／`status` 及無原生視窗的共用核心。Tk Windows 版已退役並移出原始碼與 Release；下方原 Stage 的視窗驗收保留為歷史，不宣稱新對話入口已達成所有舊 Gate。

- [x] 將初稿圖、同版次校閱、定稿圖及獨立匯出確認接到 Studio 共用流程。已驗證 CLI、MCP、改版與候選 wheel。真人宿主圖面走查及正式部署另行驗收。驗證與回滾見 `docs/test-report.md`。
- [x] 預設先擬可刪改的完整草案，初稿與定稿的預填內容逐章展示並共用批次確認。章節漏列或版次不符會阻擋定稿或匯出。專項、跨宿主與候選 wheel 已驗證。

來源為 `specs/requirements.txt`；此清單只追蹤 Studio，與舊底座 Stage 0–13 分開。
一次只有一個主要階段進行中。詳細步驟、檔案、測試與 DoD 以原文各 Stage 為準。

| Stage | 狀態 | 交付與驗收 | 相依 |
|---|---|---|---|
| 0 | 通過 | 獨立套件、Service／Storage 抽象、設定、架構／migration 文件；wheel 建置安裝與 12 項針對性測試通過，既有基線失敗單列 | 現有 checkout |
| 1 | 通過 | Project／Revision CRUD、CAS、備份遷移、原子保存；24 項 Studio 測試與 44 項相容回歸通過 | 0 |
| 2 | 通過 | 來源 CRUD／歷史／實際預覽、指紋去重、安全 ZIP；54 項 Studio 測試通過 | 1 |
| 3 | 通過 | IR v1／JSON Schema、完整節點政策、DAG／型別／來源驗證、不可變 IR；80 項 Studio 測試通過 | 1–2 |
| 4 | 通過 | Registry 版本／CRUD、能力匹配、政策與四個確定性內建工具；89 項 Studio 測試通過 | 3 |
| 5 | 本機功能通過 | Provider／文字與 Mermaid discovery／持久化串流／保守預算；108 項測試通過，真實模型品質待整體 Gate | 2–4 |
| 6 | 通過 | 資料夾證據圖、受控樣本重播、候選 IR／角色／未知、版本化 Golden Cases；116 項測試通過 | 2–5 |
| 7 | 本機 DoD 證據已核對 | 兩個固定 Golden Case 與生成 CSV／JSON Handler 在隔離 Runtime 執行；原介面不變，未支援 IR 明確拒絕；真實轉換品質留在整體 Gate | 3–6 |
| 8 | 本機功能通過 | 已補 JSON 比較、交易回復、累積修復與 Provider／UI 接線；fixture 與真正 Runtime 重播通過，固定 20 故障與真實模型品質待整體 Gate | 7 |
| 9 | 本機功能通過 | 分支雙路徑／共用輸出／決策紀錄、Runtime LLM 串流／結構／授權／預算；143 項測試通過，付費模型未驗 | 7–8 |
| 10 | 實驗實作／P2 停用 | 真實 FFmpeg 預覽、時間／比例／來源保存、vision Provider 與保守融合；8 項專項測試通過，真實理解品質未驗 | 2、5–6 |
| 11 | 本機功能候選 | Tk／DPI／來源／發布已實測；模型修復、版本差異續編與案例編輯接線及服務測試已補，新視窗視覺與整體真人 Gate 仍待 | 1–10 |
| 12 | 驗收進行中 | 五項本機基準已具證據，含 20 固定故障及三個真正程序中止；20 範例／三平台與完整 AC 仍待 | 功能階段 |
| 13 | 待執行 | 使用／開發／隱私／限制／升級文件、發行包、乾淨安裝與完整性驗證 | 12 |

Stage 12 已備妥 acceptance-v1 的 20 件固定案例（21 訓練、40 未見），尚未執行真實模型／真人評分；
規則及結果紀錄方式見 [案例驗收指南](docs/acceptance-corpus.md)。

所有 P0/P1 必須實證通過；20 件低風險案例至少 16 件非技術自行完成。
IR 20 節點靜態檢查 p95 <500 ms；100 個來源清單不得觸發 LLM。
跨平台未執行須單列，正式發布需授權，不因 devloop 自動上傳或付費。

## 完整性審核待補

- 原規格逐項核對與下一步見 [需求審核](docs/requirement-audit.md)，不得以上表舊階段通過取代最終 Gate。
- 核心階段的 DoD 與原指示檔名差異見 [階段驗收核對](docs/stage-acceptance.md)，本機基準見 [量測報告](docs/acceptance-benchmarks.md)。
- 上表通過項目代表當時的階段測試證據，不能取代全規格驗收。
- 已補「缺少工具時生成新 Handler 與測試」的受限 Python 資料處理路徑，並實跑 CSV／JSON 新算法與獨立案例。
  尚須以真實模型及完整需求矩陣驗證，不把 fixture 或目前支援範圍當成全功能完成。
- 真實模型的文字／Mermaid 各五件、20 件低風險使用者轉換及三平台 Gate 尚未驗收。
- 最新使用者要求先修復 UI；Windows 原生畫面已檢查，詳見 docs/ui-review.md。
- 發布中斷核對與結束未生效準備已接線，涵蓋發布／停用／舊準備被取代及檔案保留；
  服務與 UI model 驗證見 docs/test-report.md，新核對視窗仍待實機驗收。
- 資料夾角色／關係決策及歷史預覽已接線；採用前重核版本與內容，人工確認與流程／案例同交易保存。
  詳見 docs/folder-review.md；新視窗實機驗收仍待。
- 固定 Runtime 試做可加入為機器 verified 證據，重新核對原套件、案例及實際產物；
  流程內容改變後原驗證狀態失效，詳見 docs/machine-evidence.md。真實模型及完整使用者 Gate 仍待。
- 靜態手繪圖片已接入一般 Discovery，保留完整解析度、移除 metadata、明確預覽送出範圍與圖片用量預留；
  未知問題保存待確認草稿，詳見 docs/image-discovery.md。真實辨識品質與新增介面仍未驗收。
- 背景重讀已保護未儲存目標文字，外部更新保留編輯基準並提供比較／明確提交；
  已補非桌面控制器與 SQLite 測試，詳見 docs/project-drafts.md，新比較視窗待實機驗收。

## 回滾

Stage 0 僅新增套件及測試，無使用者資料。後續資料階段先備份 Studio 狀態，
回復相容程式與資料版本；保留 Runtime、原始來源與已發布版本。
