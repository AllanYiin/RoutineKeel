# Studio 候選包安裝與驗證

## 目標與前置條件

適用 Studio 0.1.0 候選 ZIP。此包包含 Runtime、Studio、依賴清單、啟動器、來源快照及文件，
供安裝與操作驗收；尚未正式發布。原生視窗、真人／真實模型及最終交付 Gate 仍須完成。
Windows 的非桌面安裝、案例試做及獨立 Runtime 已有證據；其他平台以後續 CI 實際結果為準。

需要可寫入的本機目錄與首次安裝的網路。啟動器會下載經檢查的 uv、Python 3.12 及依賴，
不會自動呼叫模型。未提供完全離線的首次安裝包；已有環境通過檢查時會重用。
Windows 建議使用較短的路徑，避免工具的暫存／快取路徑超過支援長度。

## 安裝流程

### 步驟 1（Step 1）：核對並解壓

從授權的維護者取得 `workflow-studio-0.1.0.zip` 及同次產出的 SHA256SUMS，核對 ZIP 的 SHA-256。
解壓到新的資料夾，保留 `workflow_studio` 下的全部檔案及目錄，不覆蓋舊安裝。
SHA-256 檢查完整性，不代替發行者的數位簽章。

### 步驟 2：啟動候選工作台

- Windows：雙擊包內 `open_studio.bat`。
- macOS：開啟 `open_studio.command`；若系統要求確認來源，先由管理者核對，不關閉整體安全控制。
- Linux：執行 `sh open_studio.sh`，需有可用的互動桌面與 Tk 系統支援。

先等待環境準備結束，才會開啟 Studio。這些是提供的入口，不代表本次已完成各平台原生視窗走查。
初始化後專案預設保存在解壓目錄的 `share_states/workflow_studio`。
要指定其他資料位置，可在啟動器加 `--workspace`；相對路徑以呼叫時的目前目錄為基準。

### 步驟 3：完成第一個案例

依[使用者指南](user-guide.md)匯入包內 `examples/first-copy`，試做、核對成果，再發布到練習工作區。
升級前先按[備份指南](upgrade-and-rollback.md)保存資料；不要直接刪掉安裝資料夾來「重裝」。
Runtime 在 `.venv`，Studio 在 `.studio-venv`，兩者不能任意混裝套件。

## 不開視窗的驗證

維護者可以先只準備環境，再查版本；以下不會開啟桌面：

```powershell
.\open_studio.bat --prepare-only
.\open_studio.bat status
```

```sh
sh open_studio.sh --prepare-only
sh open_studio.sh status
```

`--prepare-only` 成功會回傳 ok=true 與兩個 Python 路徑；status 顯示 version=0.1.0、schema_version=14。
啟動前核對套件清單／hash；環境 marker 還必須配合實際 Python prefix、版本、import 與隔離檢查。
更新失敗時保留失敗環境並還原舊 Studio 環境，未改動專案資料。
Runtime 自身的環境維護仍使用底座原有 bootstrap 規則。

## 維護者建置與重跑

從已準備 uv 的儲存庫根目錄執行：

```text
python tools/release.py --project studio
python routinekeel/workflow_studio/scripts/verify_distribution.py dist/release/workflow-studio-0.1.0.zip --require-clean --output dist/studio-install-evidence --work-dir dist
```

輸出為 `dist/release`、`dist/workspace/workflow_studio` 及 `dist/wheels`。
既有不帶 `--project` 的命令仍只建立底座及原有兩個流程 ZIP；單元件建置會如原契約取代這三個 dist 子目錄，
若要同時保留兩批產物，先把上一批複製到另一個輸出目錄，不要把使用者資料放進 dist/workspace。

包內 `bundle.json` 記錄版本、來源 commit、source_dirty 及逐檔 hash；`bundle.sha256` 供 POSIX 初始檢查。
來源只取 Git 清單並額外排除 specs、私人設定、狀態、成果、日誌、快取及連結。
wheel 的實際模組成員與 bytes 須與追蹤來源完全一致，拒絕額外未追蹤檔案或測試機路徑。
文件中的測試機絕對路徑在打包時替換；source.zip 是可建 wheel 的來源快照，完整發行工具／測試仍以儲存庫為準。
驗證器在新目錄實際走啟動器、兩個環境安裝、重複啟動、案例試做／發布／重開、獨立 Runtime 與損毀拒絕。
CI 使用 `--require-clean`，不把未提交建置當成可追溯版本；紀錄上傳僅含合成驗收資料。

## 故障排除（Troubleshooting）與限制

| 問題 | 處理方式 |
|---|---|
| 完整性不符 | 停止啟動，重新取得整包並核對 SHA256SUMS；不要手動改 bundle hash |
| 無法建立 Python／快取且出現路徑錯誤 | 保留日誌，改在較短、可寫入且非網路磁碟的目錄解壓後再測；不改寫既有工作區 |
| Runtime 環境混有 Studio 或版本不符 | 保存資料與舊資料夾，再用新的解壓目錄準備環境 |
| 缺少 Tk 或無互動桌面 | 先以 --prepare-only／status 判斷環境；請管理者補足平台支援，不能把 headless 成功當 UI 成功 |
| 環境準備被另一個程序占用 | 等該次操作結束；不要刪除活躍鎖檔或並行覆寫環境 |

不支援的原生平台、首次網路失敗、簽章提示及真人操作仍須個別驗證。
此候選包不會自動發布到 GitHub，也不會替使用者核准模型費用或外部資料傳送。
