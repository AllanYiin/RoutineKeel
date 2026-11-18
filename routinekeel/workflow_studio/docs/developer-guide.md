# Studio 開發與擴充參考

## 概覽與版本範圍（Version scope）

適用 Studio 0.1.0、Studio schema v014、Runtime API 0.2。程式 metadata 允許 Python 3.11–3.13，
目前平台 CI 使用 Python 3.12；其他 Python 小版本不能據此宣稱已驗收。
Studio 是設計期套件，Runtime 不需安裝 Studio。請先讀本子樹 AGENTS.md 及[架構](architecture.md)。

## 安裝與啟動參數

以下從儲存庫根目錄執行，前置條件是已有 `uv` 與可用的 Python 3.12。
首次同步依賴可能連線下載；這與 Studio 專案資料外傳是兩回事。
兩個環境各自安裝，不把底座的測試依賴當成 Studio 的發行依賴：

```text
uv sync --locked --python 3.12 --project routinekeel/flow_base --group dev
uv sync --locked --python 3.12 --project routinekeel/workflow_studio --group dev
uv run --locked --project routinekeel/workflow_studio --group dev python -m workflow_studio --workspace dist/studio-check status
```

Windows 啟動指定的練習工作區：

```text
uv run --locked --project routinekeel/workflow_studio --group dev python -m workflow_studio --workspace dist/studio-check --runtime-python routinekeel/flow_base/.venv/Scripts/python.exe ui
```

macOS／Linux 將 Runtime 路徑改為 `routinekeel/flow_base/.venv/bin/python`。
UI 需要含 Tk 的 Python 與互動桌面；無顯示器的 CI 不證明 GUI 可用。
現有 `open_studio.bat` 是原始碼開發入口，固定使用儲存庫工作區；它不是尚待完成的正式安裝器。

| CLI 參數／命令 | 語意 |
|---|---|
| --workspace | 專案工作區；預設目前目錄。開啟會建立或遷移 Studio 資料庫，升級前先備份 |
| --runtime-python | 已安裝相容 flow_base 的 Python；保留 venv 執行檔路徑，不解析其 symlink |
| ui | 預設命令，開啟工作台 |
| projects | 列出含封存專案的摘要 |
| status | 讀取版本與 schema；不是不寫入的資料庫檢查器 |

## 共用服務與回應

目前 CLI 只提供上表入口；完整操作由本機服務與專責服務物件提供，沒有啟動 HTTP Server。
以下 Example 不呼叫模型，使用新練習工作區建立一個專案：

```python
from workflow_studio.service import StudioService
from workflow_studio.contracts import StudioError

with StudioService.open_workspace("dist/service-example") as studio:
    try:
        project = studio.projects.create(
            "練習", {"description": "完整複製檔案", "success_criteria": ["成果 bytes 相同"]}
        )
        print(project.project_id, project.version)
    except StudioError as exc:
        print(exc.as_response())
```

`projects`／`sources` 負責版本化資料；`save_ir`、`compile`、`verify`、`model_repair` 共用已保存的專案。
`Approvals(studio)`／`Releases(studio)` 分別處理核准與發布，不能直接寫表略過驗收。
更新必須帶查詢取得的預期版本，或操作要求的內容指紋；發生衝突時重新讀取並由呼叫者決定，不能盲目重試。
資料物件、成功 dict 與 StudioError 是 Python 介面的回應；`as_response()` 產生結構化錯誤，
不要假設所有內部方法都直接回傳 HTTP 風格 dict。

## 擴充工具與 Handler

1. 先檢查 `registry/builtin.py` 與 `Catalog.available(policy)`。已核准描述仍可能缺少受控實作。
2. 新工具描述使用 `registry/models.py` 的 `ToolDescription`，明列輸入／輸出型別、版本、權限、
   網路、資源、成本、implementation 與 test_evidence。新版本另存，不能改寫已核准契約。
3. 可用現有資料語言表達的算法，走 `transform(text, params)` 與至少兩組合成測試，
   再用獨立 Golden Cases 驗證。語法與配額見[生成 Handler](generated-handlers.md)。
4. 已通過的生成處理可經 `HandlerLibrary` 保存固定快照，再核准給同工作區其他專案使用；
   重用專案仍須自己的案例。操作與參數覆寫規則見[工具庫](handler-library.md)。
5. 真正新增底層受控實作時，須一起修改 Registry 能力檢查、Compiler 支援判斷／Lowering、
   Runtime 可信模板及獨立 Verifier 的重建檢查。只登錄描述或 `module.function` 不會取得任意執行權限。
6. 對相依、分支兩路、錯誤輸入、權限拒絕、零不必要模型呼叫與舊模板相容執行測試，
   再以新工具版本進入核准流程。原生 Runtime Handler 的核心合約以 flow_base 的既有公開介面為準。

## 驗證與錯誤

```text
uv run --locked --project routinekeel/workflow_studio --group dev python -m pytest tests/studio -q -rs -k "not test_real_tk_buttons and not test_tk_analysis and not test_ui_state_multiline"
uv run --locked --project routinekeel/workflow_studio --group dev ruff check --config routinekeel/workflow_studio/pyproject.toml routinekeel/workflow_studio/src routinekeel/workflow_studio/scripts tests/studio
uv run --locked --project routinekeel/workflow_studio --group dev ruff format --check --config routinekeel/workflow_studio/pyproject.toml routinekeel/workflow_studio/src routinekeel/workflow_studio/scripts tests/studio
```

這組命令明確排除原生視窗；另外安排真人走查，不能以控制器測試替代。
CI 與重跑資料集見[平台驗收](platform-acceptance.md)、[固定案例](acceptance-corpus.md)。
`IR_UNSUPPORTED_FEATURE` 必須保留原因，不能把不支援語意偷偷改為另一種算法；
`REVISION_CONFLICT` 要保留使用者草稿；`TEST_FAILED` 不可進入核准與發布。
事件完成與成果提交必須同交易，實作及故障測試見[操作事件](operation-events.md)。

尚未驗收範圍與正式包缺口見[已知問題](known-issues.md)。任何範圍變動都要更新相關測試、文件與版本證據。
