# 維護與打包

在儲存庫根目錄執行以下指令。維護者需要 Python 3.12 及 uv。

```sh
python tools/release.py
uv run --locked --project routinekeel/flow_base --group dev pytest tests routinekeel/flows -q
```

`dist/workspace/` 是供測試的已安裝套件布局，`dist/release/` 是待上傳的獨立 ZIP、`SHA256SUMS` 與 `versions.json`。每次建置會重新產生這些目錄。若只建底座，用 `python tools/release.py --project flow_base`；流程可用中文目錄名稱或其 Python 套件名選取。執行完整測試前請先打包所有元件。

新增工作流時，在 `routinekeel/flows/` 建立使用者看得懂的子目錄，提供 `pyproject.toml`、`flow.template.json`、`README.md` 與程式碼。打包工具會發現有模板的流程，檢查套件與模板版本，並生成相應入口。可在 `[tool.routinekeel]` 的 `archive-name` 指定唯一的英數下載名稱。更改執行邏輯時，更新相應套件版本與模板、鎖定檔。

## GitHub 發行

PR 開啟或更新時會編譯所有套件並跑測試。PR 合併到 `main` 後，Actions 用當次合併提交重新建置、驗證，然後建立該提交專屬的 Release。只有測試成功才上傳附件，所有附件上傳完才公開該私有儲存庫內的草稿 Release。單純關閉 PR 不會發行。

Release 的 Git 標籤為 `build-<完整提交 SHA>`，套件 ZIP 各自帶元件版本。相同提交的重跑會沿用原標籤，已發布的 Release 會保留。若首次導入或需要補發，可在 Actions 的 **Build and release packages** 選 `main` 並按 **Run workflow**。

PR 驗證工作以唯讀權限執行。發行工作使用儲存庫的 GitHub Actions token 上傳附件。發布失敗可先查看 Actions 中的 job 紀錄；草稿留在 Releases，修正後重跑。

使用者更新時只需從 Releases 下載所需元件 ZIP。各元件內的 `source.zip` 供離線查看該元件；要建置及測試整個專案，請從同一提交取出完整 GitHub 原始碼。
