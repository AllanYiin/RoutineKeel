# 維護與打包

在儲存庫根目錄執行以下指令。維護者需要 Python 3.12 及 uv。

```sh
python tools/release.py
uv export --frozen --project "workflows/FLOW_課程影片產生字幕/src/ai_flow01/profiles/asr-cpu" --no-dev --no-emit-project --output-file dist/subtitle-test-requirements.txt --quiet
uv run --locked --project flow_base --group dev --extra mcp --with-requirements dist/subtitle-test-requirements.txt python -m pytest tests workflows --ignore=tests/studio -q
uv run --locked --project workflow_studio --group dev python -m pytest tests/studio -q
```

`dist/workspace/` 是供測試的已安裝套件布局，`dist/release/` 放全包、獨立 ZIP、`SHA256SUMS` 與 `versions.json`。全包解壓後的 `workflows/` 初始為空，參考流程 ZIP 收在 `examples/workflows/`。每次建置會重新產生這些目錄。用 `python tools/release.py --project flow_base` 可單獨建置底座。流程可用中文目錄名稱或 Python 套件名選取。執行完整測試前請先打包所有元件。

新增工作流時，在 `workflows/` 建立使用者看得懂的子目錄，提供 `pyproject.toml`、`flow.template.json`、`README.md` 與程式碼。打包工具會發現有模板的流程，檢查套件與模板版本，並生成相應入口。可在 `[tool.routinekeel]` 的 `archive-name` 指定唯一的英數下載名稱。更改執行邏輯時，更新相應套件版本與模板、鎖定檔。

## 建立本機候選包

執行 `python tools/release.py --candidate rk-next-0.3.0-rc1`，取得獨立的 `dist/candidate-rk-next-0.3.0-rc1/release/`、`workspace/` 與 `wheels/`。每個候選名稱對應一份建置，名稱已存在時請使用另一個名稱以保留兩份版本。完整工作區 ZIP 包含使用指南、底座、Studio 與兩個參考流程。用 `SHA256SUMS` 核對下載內容，從 `versions.json` 查閱版本、大小與組成。

升級前停寫並備份工作區，特別是底座的 `state.sqlite3`、Studio 的 `studio.sqlite3`、來源、流程與成果。底座升級至 schema v005，Studio 使用 schema v15。只回退已安裝工作流套件時可用該次更新收據。降版元件與資料庫時，在另一個空目錄還原同一份工作區備份，核對資料庫完整性、已安裝版本及代表性成果，再切回使用。

## GitHub 發行

現有工作流程將驗證與發布分開。正式發布需要在 `main` 手動啟動 `Build and release packages`、勾選 `publish_release`，並通過發布前置檢查。本機候選不會啟動這個流程。遠端產物與此處的驗收證據須以相同提交及套件雜湊比對。[目前支援範圍](../flow_base/docs/known-issues.md)

使用者更新時只需從 Releases 下載所需元件 ZIP。各元件內的 `source.zip` 供離線查看該元件；要建置及測試整個專案，請從同一提交取出完整 GitHub 原始碼。
