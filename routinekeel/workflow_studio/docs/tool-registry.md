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
| control.branch 1.0.0 | 有限確定性分支 | 兩路必須有共同輸出契約 |
| llm.structured 1.0.0 | 結構化模型處理 | 另需網路、模型、金鑰與預算授權 |
| data.transform_text/csv/json 1.0.0 | 受限資料語言 Handler | 依節點保存程式與測試，非任意 Python |

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

## 工具管理操作

工作臺上方「工具管理」提供技術管理入口，作用於整個工作區：

- 查看每個版本的契約、hash、測試依據、支援限制及目前政策下的可用性。
- 以選取版本修改名稱、能力、平台、權限與資源宣告，另存新版本；舊內容不變。
- 匯入不超過 128 KB 的 UTF-8 ToolDescription JSON；拒絕重複 key、非物件及自授 trust_status。
  匯入只保存描述，不會讀取或執行 implementation／test_evidence 指向的程式。
- 對已保存契約填寫決策者及理由後核准、撤回核准、啟用、停用或封存。
  UI 同時綁定 contract_hash 與 record_version，過期審閱不能蓋過新狀態。

新描述一律 untrusted。啟用不等於核准，核准也不代表 Compiler 已有適配。
`Catalog.allowed(policy)` 表示通過信任與權限政策；`available(policy)` 還要求有受控實作及相容契約。
Discovery、修復與匹配只提供 available 工具。已知實作不得藉別名低報最低權限、網路需求或成本，
節點的網路宣告也必須與工具一致。未知實作可保存描述，但不會因核准而得到任意程式執行權限。

工具名稱依 ID@version 顯示，建立新版本不會改寫舊流程的名稱與契約。
停用／撤回阻擋 Studio 的後續編譯、驗證與發布；已安裝的獨立流程仍可執行，需另行停用發布版本。
舊核准與狀態紀錄保留於 v011 工具事件表。未提供操作者的舊 API 不會被偽裝成人工決策。
升級快照只證明升級時的狀態，原核准者與理由仍由舊 approval 表提供。

模型會收到可用工具的能力、型別、權限、資源與成本宣告，不額外傳送本機測試依據路徑。
這不是測試品質自動認證；核准者仍需核對 test_evidence，生成 Handler 的實際重用與第三方適配另須驗收。
新工具管理視窗尚未做實機互動與視覺驗收。
