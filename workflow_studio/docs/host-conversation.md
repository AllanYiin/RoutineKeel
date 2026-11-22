# 在對話內設計工作流

在 Codex 或 Claude Code 開啟 `workflow_studio/`，先讀 `AGENTS.md`，用一句話說明要完成的任務。
宿主負責草擬、提問、收取修正與保存實際回答；Studio 提供專案、來源、版本、合約、建置及驗證核心。
不開啟 Tk 或其他獨立視窗，不要求設計期 API 金鑰。

引導順序：一句任務 → 釐清 → 步驟骨架 → 輸入輸出 → 工具清冊 → 檢核清冊 → 審核方式 → 任務設定檔 → 藍圖包。

每層先由宿主提出有依據的草案，再請作者修正與確認。問題只涵蓋當前必要決策；工具回傳 JSON 不代表使用者已回答。
使用宿主可用的提問介面，也接受直接文字修正、中途插話、取消與恢復。未回答不能視為同意。
第 3 層須包含本次製作重點與方向提醒；第 7 層區分每次輸入、設定一次、Agent 當下判斷並記錄理由。
技能須整份保存並標示對應步驟；未知工具能力保留為缺口，安裝時才綁定當地可用工具。

藍圖是設計資料，不代表可執行成品或已核准安裝。宿主整理藍圖、合約、依賴、技能及缺口後，
依底座的 `install-workflow` 介面另行試做與安裝；付費、外傳、發布與真人審核沿用各自的授權要求。

命令檢查：

```powershell
uv run --project workflow_studio python -m workflow_studio guide
uv run --project workflow_studio python -m workflow_studio --workspace <RoutineKeel根目錄> projects
uv run --project workflow_studio python -m workflow_studio --workspace <RoutineKeel根目錄> status
```

安裝包可使用 `studio.bat guide` 或 `sh studio.sh guide`。`guide` 只輸出引導資料，不建立資料庫、不呼叫模型、不開視窗。
它不代表已提供自動保存逐層答案的訪談狀態服務；宿主仍須保存對話與實際確認。
需要操作既有專案核心時，使用 `workflow_studio.conversation.session.StudioSession` 或 `StudioService` 的公開方法。
核心寫入維持版本檢查；來源內容不得變成執行權限。舊 Tk 草稿不自動轉換為已確認的訪談答案。
