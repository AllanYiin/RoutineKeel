# 檔案複製範例：一鍵安裝與啟動（0.2.2）

將本資料夾與 flow_base 0.2.7 放在同一層。Windows 雙擊 run_app.bat；macOS 開啟 run_app.command；Linux 執行 sh run_app.sh。入口自動交由共用底座準備必要環境、依賴與模型，再進入本流程；不必先安裝 uv 或 Python。

資料夾可自由命名，包括中文、空格或 ai_flow_ASR_video；不要為改名而修改 flow.json 的 id 或 wheel 名稱。視窗中的名稱來自 flow.json 的 title。安裝完成後輸入 /input 檔案路徑，確認計畫後輸入 /run 開始。安裝本身不呼叫 LLM，也不開始處理影片。

首次需要網路，資源集中在共用 share_states。第二次沿用可用環境。缺少同層 flow_base 套件時會提示放置方式；底座程式不重複放入每個流程包。支援 --prepare-only、--offline、--config 設定.json；完全禁止底座啟動下載另設 UV_OFFLINE=1。

更新舊包時，將本包內容覆蓋到既有流程資料夾；勿同時保留兩份相同 id 的流程。保留共用 share_states 與 share_logs，可延續歷史。三個入口由底座生成，流程作者只維護差異設定與任務實作。

# ai_flow02：第二個獨立流程

這是確定性的檔案複製範例，用來驗證「新增任務只加一個資料夾」。不使用 ASR、模型或 LLM，也不建立任務專屬環境。

解壓後與 `flow_base`、`ai_flow01` 同層。首次發現時自動登錄自己的 flow_id，每次執行建立新的 run_id。

```sh
sh ./flow_base/start.sh ask '複製「講義.txt」' --execute
sh ./flow_base/start.sh run --task ai_flow02 --input "講義.txt"
sh ./flow_base/start.sh history --task ai_flow02
```

Windows 改用 `.\flow_base\start.ps1` 作為相同命令入口。產出 `artifacts/result.bin`，其內容與輸入檔案一致，另有帶識別碼與快取紀錄的 report.json。相同輸入與輸出重跑會命中快取。此包是擴充範例，不代表另一個 AI 模型應用。

`flow.json` 宣告能力與自然語句，wheel 只包含這個流程的設定與 handler，`source.zip` 提供完整可修改原始碼。新增自訂任務時換成自己的 namespace、manifest 與 typed configuration；共用底座不需重製。


新版 0.2.2 配合底座 0.2.7，省略 --output 時放本流程 outputs/<工作>/。複製成果位於 artifacts/result.bin；docs/run-summary.md、report.json、deliverables.json 提供摘要與索引。無產生腳本時不建立 scripts。已有平面結果保持原格式。
