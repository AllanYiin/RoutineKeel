# 維護與打包

這份文件給維護者使用。一般學員請回根 README，下載 ZIP 並開啟流程入口。

## 目前快照

| 元件 | 版本 |
| --- | --- |
| flow_base | 0.2.10 |
| 課程影片產生字幕（ai_flow01） | 0.3.4 |
| 檔案複製範例（ai_flow02） | 0.2.2 |

本次將已驗證的分包整理為 RoutineKeel 儲存庫，沒有改動三個 runtime wheel。原版完整測試為 221 passed；驗證細節在 source/base/VERIFICATION.md 與 source/ai_flow01/VERIFICATION.md。

## 原始碼維護

在 source/ 下保留既有相對目錄，讓打包工具及測試不需要因品牌更名而重寫。開發環境需要 Python 與 uv；這是維護者需求，與學員的一鍵安裝流程不同。

從 RoutineKeel/source 執行：

```sh
python3 tools/release.py
uv run --project base --group dev pytest tests ai_flow01/tests -q
python3 base/scripts/apsm_validate.py --project base --strict
```

先產生 dist/workspace 下的分包，因為部分整合測試會驗證實際 wheel、啟動腳本與 ZIP。若環境提供 PowerShell，可設定 FLOW_BASE_TEST_PWSH 為其完整路徑以執行對應測試；沒有時必須如實記錄哪些測試跳過。不要把 Linux 測試當成 Windows／macOS 真機驗收。

只建一包可使用 `python3 tools/release.py --project base`（或 ai_flow01、ai_flow02）。改動元件時更新該元件版本及必要鎖定檔，再打包。不要以同一版本號默默替換執行邏輯。

## 更新儲存庫內的發行快照

source/dist/workspace 是建置暫存；不要提交它。發布時將其中 flow_base、ai_flow01、ai_flow02 的**程式發行檔案**分別更新到根層 flow_base、課程影片產生字幕、檔案複製範例。根層對應名稱刻意讓使用者看得懂；Python namespace 不必更名。

僅在乾淨的發布 checkout 做這一步，不要覆蓋正在使用的工作區、已註冊的 flow.json 或私人 settings.json；發行快照不夾帶本地 flow_id、執行紀錄、outputs 或模型。

根層快照更新後，在 RoutineKeel 根目錄執行：

```sh
python3 source/tools/bundle_routinekeel.py
```

此工具只讀取明列的發行檔案，將它們封裝在 ZIP 的 RoutineKeel/ 根目錄，並更新 downloads/SHA256SUMS 與 versions.json；不打包 .venv、share_states、share_logs、settings.json 或 outputs。名稱跟隨底座版本，元件精確版本另列在 versions.json。原本 tools/release.py 的開發用 bundle 名稱保留相容；對學員發布使用此品牌化 ZIP。

下載包包含三個獨立套件、Logo 與上手文件。GitHub 儲存庫則另外包含可瀏覽的完整 source/。README 中的相對連結會隨 repo 搬移，無需公開私人儲存庫才能分享下載好的 ZIP。

## 版本控制與個人資料

.gitignore 排除環境、執行狀態、日誌、輸出、私人設定、常見媒體與模型檔。正式提交前仍需檢查暫存區；忽略規則不會自動移除曾經追蹤的私人檔案。

Logo 原檔位於 assets/routinekeel-logo.png。不要上傳來源影片、使用者的課程結果、Codex Session 收據、登入資料或 API 金鑰。原始碼測試中的合成收據與示範文字不代表真實帳務或課程成果。
