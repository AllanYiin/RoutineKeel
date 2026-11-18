# Wheel 乾淨安裝預檢

## 概覽與版本（Version）

2026-11-18，來源 commit `1137be9`，Studio 0.1.0／flow_base 0.2.12，Windows／CPython 3.12.11。
這是正式發行工具實作前的 wheel 安裝證據；不代表非技術安裝器、原生視窗或整體產品已驗收。
產物及機器證據留在 `dist/studio-preflight/`，未對外發布。

## 安裝參數與命令

從儲存庫根目錄匯出兩套不含 dev 的鎖定依賴，保留下載檔案 hash。Example：

```text
uv export --locked --no-dev --no-emit-project --no-header --no-annotate --project routinekeel/workflow_studio --output-file dist/studio-preflight/studio-requirements.txt
uv export --locked --no-dev --no-emit-project --no-header --no-annotate --project routinekeel/flow_base --output-file dist/studio-preflight/runtime-requirements.txt
uv build --wheel --out-dir dist/studio-preflight/wheels routinekeel/workflow_studio
uv build --wheel --out-dir dist/studio-preflight/wheels routinekeel/flow_base
```

實際命令另外明確指定既有 Python 3.12.11 執行檔；uv 為 0.13.6，cache 在本次工作樹的 dist/uv-cache。
分別新建 `studio-env`／`runtime-env`，以 `uv pip install --require-hashes --no-deps -r ...`
安裝完整鎖定依賴，再 `--no-deps` 安裝對應本機 wheel。不是從既有 .venv 複製套件，沒有 editable install。
兩邊 `uv pip check` 通過；Studio 共 8 個 distributions，Runtime 共 7 個，未安裝 optional MCP。

| wheel | bytes | SHA-256 | 成員數 |
|---|---:|---|---:|
| workflow_studio-0.1.0-py3-none-any.whl | 229013 | `0fd2eeec5365c201d835a13f21c4885f5b80e3cf55e316458aaea8afa7700b8e` | 106 |
| flow_base-0.2.12-py3-none-any.whl | 147755 | `781cef5941422189f84677e7243bd5349a10cb098e0526df72b9d41cf3fa6f68` | 52 |

逐項核對包內來源檔 bytes 與本次來源一致，ZIP 無損毀／重複成員，保留可信舊模板與 runtime_runner。
清單未包含 .env、.venv、__pycache__、狀態／日誌目錄、資料庫、私鑰或測試機指定私人路徑。
這是確定來源清單與具名 denylist 檢查，不是能找出任意敏感內容的萬用掃描器。
完整清單：`wheel-inventory.json`。

## 回應與驗證結果

以新安裝的 Studio Python `-X utf8 -I` 執行檢查，沒有設定來源 PYTHONPATH。
確認 `workflow_studio.__file__` 位於新環境 site-packages，Studio 環境找不到 flow_base；
獨立 Runtime 同樣確認 flow_base 來自自己的 site-packages，且找不到 workflow_studio。

入門資料夾實際完成建案、匯入、候選採用、編譯、Golden Case 試做、工程具名核准與隔離發布。
發布包再由新的 Runtime 環境呼叫 WorkflowService plan／prepare／verify／run，
第二份不同輸入成功，registered model calls=0。從 named_outputs 的 primary 與 sidecar 契約
取得實際成果，核對內容與其 SHA-256，不靠猜測輸出檔名。
證據：`clean-workspace-132d52c20890401eb4a2117d2366fa0a/result.json`。

所有程序結束後，將完整測試工作區複製到新的還原目錄；117 個檔案在開啟前 bytes 一致。
還原副本能重開專案、來源／案例／發布紀錄，再次試做通過；原工作區完整指紋保持不變。
證據：`restore-preflight.json`。這只驗證同版本的靜止工作區複製還原，不冒充跨版本 binary rollback。

## 錯誤修正與剩餘限制

首輪檢查程式把第二份輸入放在 Runtime 工作區外，遭既有 PERMISSION_DENIED 正確拒絕；
調整為工作區內路徑後，執行已完成，但檢查程式猜測 a.txt／result.txt 而找不到真正 sidecar。
最後改依 named_outputs／primary 中的 result 契約驗證。沒有為了通過而改動底座權限或輸出契約。
獨立程序用 `-X utf8 -I` 固定中文 console 編碼，不依賴被 isolated mode 忽略的 PYTHONIOENCODING。

尚缺完整發行 ZIP／啟動器、三平台乾淨安裝、原生 UI 啟動、使用者照指南操作及真正跨版本回退演練。
這次 wheel 預檢時，`tools/release.py` 尚未接入 Studio。後續候選 ZIP／啟動器及新的驗收範圍
見[安裝指南](distribution.md)；不能只用本頁的兩個 wheel 證據宣稱一鍵交付已完成。
