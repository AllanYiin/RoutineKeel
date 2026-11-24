# 固定驗收資料 acceptance-v1

20 件低風險合成案例：文字、Mermaid、成功資料夾、流程圖片各 5 件。

- `C01`–`C20/authoring/`：給參與者的工作說明與圖示。
- `C01`–`C20/training/`：可供模型分析及初次試做的輸入／預期成果。
- `C01`–`C20/held_out/`：觀察者在候選固定後才加入的獨立驗收資料，不送給模型。
- `catalog.json`：比較規則、檔案指紋與清單。
- `results-template.json`：目前全部 not_run，不是成功結果。

請遵守 [完整操作與評分規則](../../../../workflow_studio/docs/acceptance-corpus.md)。
不要將本根目錄整批匯入分析；參與者只使用指定的 authoring 與 training。
