# 統一目錄與回復

GitHub 原始碼、Release 解壓結果與本機安裝使用相同的元件相對位置：

```text
RoutineKeel/
├── .venv/                         # 首次啟動建立，不提交或打包
├── flow_base/
│   ├── share_logs/                # 執行紀錄，不提交或打包內容
│   └── share_states/              # 狀態、快取與模型，不提交或打包內容
├── workflows/
│   └── FLOW_<流程名稱>/
├── blueprints/                    # 藍圖來源，尚未轉成可執行流程
└── workflow_studio/
```

原始碼另保留 `tests/`、`tools/`、`docs/`、`assets/` 與 CI 設定。
Python import 名稱、flow_id、job_id 與 run_id 不因資料夾前綴而改名。
`flow_base/components/blueprint_media/` 是底座可選的獨立媒體套件原始碼。
私人藍圖、已安裝流程、設定、模型與既有成果不會自動加入 GitHub。

從根目錄執行 `python tools/release.py`，產生四個元件 ZIP，以及
`RoutineKeel-workspace-<底座版本>.zip` 全包。全包解壓得到 `RoutineKeel/`；
分包則解壓至同一個 `RoutineKeel/` 內，保留 `workflows/FLOW_…/` 路徑。
`.venv/` 不隨 ZIP 分發；共享環境由底座啟動器在根目錄建立。

舊版平面工作區仍可執行，不能只建立空的 `workflows/` 就宣稱完成遷移。
更新既有流程以識別碼配對並保留原資料夾，兩種 ZIP 都可作為更新附件。
不要直接將新 ZIP 覆蓋到正在使用的舊安裝；先結束工作並保存完整備份。
有排隊或非終態 Run 時，套件更新會拒絕。

本次 E 工作樹的搬移前檔案、原有 staged／unstaged diff 與搬移對照保存在
`dist/layout-source-backup/`；原發行包保存在 `dist/release-backups/`。
回復前先另存目前檔案與狀態，依 `moves.json` 將目錄反向搬回，
再使用 `source-before.zip` 核對或還原搬移前原始碼。不要使用 `git reset --hard`
丟棄使用者既有修改。資料庫需使用成組備份，不能只回復程式或單一索引。

驗證命令（Python 3.12）：

```powershell
python tools/release.py
uv run --project flow_base --group dev python -m pytest tests workflows --ignore=tests/studio -q
uv run --project workflow_studio --group dev python -m pytest tests/studio -q -k "not test_real_tk_buttons and not test_tk_analysis and not test_ui_state_multiline"
```

GitHub 發布仍經 `.github/workflows/packages.yml` 的三平台測試與手動發布 Gate。
