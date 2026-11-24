<p align="center">
  <img src="assets/routinekeel-logo.png" alt="RoutineKeel Logo" width="720">
</p>

# RoutineKeel

## 簡介

**在熟悉的桌面對話裡，把經常做的事整理成可以重複執行、驗收與改善的本機工作流。**

例如把課程影片轉成字幕。你可以把音訊處理、語音辨識和真人聽審串成一個流程，再交給其他人使用。RoutineKeel 的底座負責準備環境、執行工作、保存進度和管理成果。流程作者負責自己的處理規則與驗收條件。

| 想做的事 | 可以這樣說 | 接下來會發生什麼 |
| --- | --- | --- |
| 建立工作流 | 「把品名整理成原文與大寫兩欄，交付前給我看一次。」 | Studio 草擬流程，逐步列出輸入輸出、校閱點與驗收案例。 |
| 執行工作流 | 「用剛安裝的流程處理這份檔案。」 | 宿主顯示計畫與交付項目。一般步驟接續執行，到指定校閱或授權點才等待回覆。 |
| 改善工作流 | 「這次有兩個品名被改錯，以後要保留原文。」 | 可修正這次成果，或建立新版流程，先比較驗收結果再決定是否採用。 |

## 安裝與快速開始

### 使用需求（Prerequisites）

提供 Windows、macOS 和 Linux 啟動入口。首次安裝及下載資源通常需要網路，啟動器會準備 Python 和 uv。macOS／Linux 如未安裝 uv，需有 `curl` 或 `wget`，以及 `unzip`。字幕模型的空間與裝置需求另見流程說明。 [字幕環境需求（Requirements）](workflows/FLOW_課程影片產生字幕/README.md)

1. 從 Releases 的 **Assets** 下載 `RoutineKeel-workspace-*.zip` 和 `SHA256SUMS`，核對雜湊後解壓到新的目錄。保留解壓後的 `RoutineKeel/` 結構。下載權限依儲存庫設定。 [GitHub Releases](https://github.com/AllanYiin/RoutineKeel/releases)
2. 全包包含底座與 Studio。依現行打包格式，首次解壓時 `workflows/` 為空。先選 `examples/workflows/` 裡的 `flow_copy-*.zip`，把它解壓到同一個 `RoutineKeel/` 根目錄，確認有 `workflows/FLOW_檔案複製範例/`。若現有全包已解出範例流程，可直接進入該資料夾。分包安裝時，請使用同一次發行的底座及所需的 Studio 或流程 ZIP。
3. 開啟範例資料夾裡的啟動檔。準備完成後輸入 `/input` 和檔案路徑，看過計畫再輸入 `/run`。第一次可用任何小型測試檔，以下是 Windows 路徑範例。

   ```text
   /input D:\範例\講義.txt
   /run
   ```

| 作業系統 | 從流程資料夾啟動 |
| --- | --- |
| Windows | 雙擊 `run_app.bat` |
| macOS | 開啟 `run_app.command`，首次依系統提示允許執行 |
| Linux | 執行 `sh run_app.sh` |

完成時會顯示 `run_id` 與輸出位置。複製範例的 `artifacts/result.bin` 內容應與輸入檔一致。也可從 `flow_base/` 開啟共用入口，查看全部已安裝的流程。 [檔案複製範例](workflows/FLOW_檔案複製範例/README.md)

## 使用方式

### 在桌面對話建立與安裝

1. **設計**。接好本機宿主後，在對話說明輸入、想要的成果，以及哪些地方需要你確認。Studio 顯示初稿和定稿的流程圖與逐步輸入輸出清單，保存各版的校閱回覆。定稿確認後匯出藍圖。 [Studio 對話操作](workflow_studio/docs/host-conversation.md)
2. **建置與試做**。底座依藍圖在目標環境解析工具與權限，建置候選套件，再把它放進臨時工作區實跑驗收案例。設計時還沒決定實際工具，可以先匯出藍圖，建置前再補齊。 [藍圖建置與試做](flow_base/docs/blueprint-install.md)
3. **安裝與執行**。試做通過後，底座重驗同一份候選套件與收據，再安裝到正式工作區。執行時先檢核輸入，步驟完成後再檢核具名輸出及交付條件。 [桌面對話使用指南](flow_base/docs/user-guide.md)

Studio 定稿可選「先求有再求好」或「不能有錯」。前者讓已標記可容許的品質偏差留下待修事項，流程可以繼續。後者在節點失敗時停止排程後續步驟。必要資料合約與指定人工核准會在兩種模式中執行。 [品質驗收與回饋](flow_base/docs/graph-quality.md)

### 接續工作與查看結果

- CLI 可同步執行。若由桌面宿主透過 MCP 提交到佇列，另開 `flow_base/queue_worker.*`。提交時會先取得 `execution_id`，Worker 領取後才開始執行。可用原編號在下一段對話查進度。 [MCP 連線與 Worker 設定](flow_base/docs/mcp-local.md)
- 畫面會顯示工作流及步驟的開始、完成或失敗。每個 Run 有 `run_id`，日誌可查看實際事件及停止原因。需要人工判斷時，可憑原本的工作編號回到同一待辦。
- 成果放在各流程自己的 `outputs/`，共用狀態與日誌在 `flow_base/share_states/` 和 `flow_base/share_logs/`。查看品質判定與正式交付狀態，再決定採用哪一版。流程 API 用量與可取得的 Codex 對話用量分開顯示。 [底座說明](flow_base/README.md)

## 參考流程與可重用 Pattern

| 原始碼中的流程 | 適合用來做什麼 |
| --- | --- |
| **檔案複製範例 0.2.4** | 用小型檔案確認安裝、啟動與輸出。 [操作說明](workflows/FLOW_檔案複製範例/README.md) |
| **課程影片產生字幕 0.5.1** | 在本機處理音訊並以 faster-whisper `large-v3` 辨識，產出 SRT、VTT 和可播放原音的確認頁。`REVIEW_REQUIRED` 代表待真人聽審。 [操作說明](workflows/FLOW_課程影片產生字幕/README.md) |

**可重用工作流片段（RWP）** 可由 Studio 依任務需求提出建議。在設計時核對適用條件，確認採用後接上既有流程。翻譯範例的完整流程是「翻譯者 → 批評者 → 修訂者」，Pattern 只包含批評者與修訂者。

- **單輪版**對照原文與初稿，將批評結果交給修訂者。
- **回審版**會把修訂稿交回批評者，最多兩輪。若仍未獲接受，底座保留修訂稿並等待人工處理。此路徑已在獨立工作區使用實際 Codex 子代理驗證，當次由宿主逐步派工和傳回回執。實際部署需在目標環境綁定並驗證宿主 Adapter。

翻譯校訂、文字保真與資料勾稽可隨具名候選建出選配 Pack。請依目錄內的校驗值核對，再經 Studio 登錄、試做與採用。 [Pack 與跨環境綁定](docs/specs/domain-packs.md)

## 故障排除、更新與維護

| 看到的狀態 | 可以怎麼做 |
| --- | --- |
| 工作維持 `queued` | 開啟本機 Worker，再用原 `execution_id` 查狀態。 |
| 藍圖顯示工具缺口 | 檢查目標工作區的工具安裝、宿主連線與權限，再回到同一草稿試做。 |
| 等待人工審核或交付保留 | 開啟這次工作的待辦和預覽，確認需要回覆的項目。 |
| 執行失敗 | 保留 `run_id` 或 `error_id`。可從底座用 `doctor`、`diagnose` 或 `explain-error` 查原因。 [診斷與匯出](flow_base/docs/error-catalog.md) |

更新流程前先結束相關排隊或執行中的工作。可在對話附上新版流程 ZIP，請宿主預覽變更後套用。底座會保留設定、歷史和成果，並提供更新備份供回退。搬到其他電腦時，重新準備執行環境，另行備份設定、狀態和成果。 [流程更新與回退](flow_base/docs/workflow-updates.md) · [目錄遷移](docs/layout-migration.md)

目前原始碼的底座與 Studio 版本皆為 **0.3.0**。下載使用時以 Releases 中該次發行的 `versions.json` 和 `SHA256SUMS` 為準。`flow_base/` 是共用底座，`workflow_studio/` 是設計工具，`blueprints/` 放藍圖，`workflows/` 放獨立流程。維護與測試從儲存庫根目錄進行。 [目錄結構](docs/structure.md) · [維護與打包](docs/maintenance.md)

程式碼採 MIT 授權，Logo 使用原始 PNG。 [授權全文](LICENSE) · [素材說明](assets/README.md)
