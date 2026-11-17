# 工具能力目錄

Registry 只宣告與篩選能力，不載入來源中的程式、不執行 Shell。
ToolDescription 延伸 IR ToolSignature，增加能力、資源、權限、測試依據、實作識別碼及成本。
每個 ID@version 的契約內容不可變，更新需建立新版本；停用／封存使用 record_version CAS。

初始白名單由 `Catalog.seed()` 明確加入：

| 工具 | 能力 | 範圍 |
|---|---|---|
| file.copy 1.0.0 | 原樣複製 bytes | 不需網路 |
| text.normalize 1.0.0 | UTF-8 換行與可選逐行去空白 | 僅 strip／final_newline 布林參數 |
| json.format 1.0.0 | JSON 排序、縮排 | 保留資料語意，拒絕非有限數值 |
| file.report 1.0.0 | bytes 數與 SHA-256 JSON | 不讀其他來源 |

這些函式只轉換記憶體 bytes，後續 Runtime Adapter 負責在本次工作區寫檔。
註冊其他工具預設 untrusted。`approve` 是明確的人類決策入口，記錄 actor、reason、
契約 hash 及時間；hash 變動、工具停用或無測試證據會拒絕核准。
核准代表契約可被政策考慮，不代表任意實作已取得執行權限；Compiler 還須驗證支援該實作。

ToolPolicy 預設只允許 workspace.write、無網路、無外部副作用、零宣告成本。
匹配先篩選作用域、平台與政策，再核對 capability、輸入／輸出 schema。
排序優先內建工具、無外部副作用、較低成本，最後以穩定 ID@version 排序。
無匹配時回傳空候選，由 Discovery 回報能力缺口，不自行授予權限。
seed 重跑不會重新啟用使用者已停用的版本。

Service 的 `save_ir` 只把本次政策允許的工具簽章交給 IR 驗證器。
Catalog 與政策不會因來源文字或模型的信心分數改變；核准入口不可暴露給模型自行呼叫。
