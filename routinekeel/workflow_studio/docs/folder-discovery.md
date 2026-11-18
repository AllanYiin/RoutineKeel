# 成功資料夾與 Golden Cases

`FolderDiscovery.analyze` 從既有來源版本讀取檔案副本，保存類型、大小、SHA-256、
匯入時觀察到的 mtime（若有）、候選角色、樣本比對關係、日誌檔名引用及未知缺口。
input／raw、intermediate、output／expected 等目錄名稱只用來推測角色；
使用者可明確提供角色覆寫，仍不代表演算法已被證明。

本機以四個受控內建 bytes 轉換作樣本重播；相符的關係標記 inferred，
claim 明示「可重現此樣本」不等於歷史因果或涵蓋例外。預設最多 200 次樣本比對。
時間順序與日誌只保存觀察，不直接提升成 verified；graph 不複製原始敏感日誌內容。
只有成果、缺少輸入／中間步驟或無已知工具匹配時，保留 unknowns。

`candidate_irs` 枚舉有限、無循環的 input→output 路徑，最多 20 個候選，
套用實際 Registry 契約與 Project goal，再經完整 IR 驗證。
能力或型別不相容的路徑不輸出假合法 IR；支援範圍目前限已註冊工具。
文字型資料夾的證據摘要會加入後續 Discovery 的已揭露模型資料；對外傳送仍須確認相同 request_hash。
圖片內容的模型推導尚未實作，本機仍可保存其類型／指紋／未知角色。

EvidenceGraphs 支援讀取、確認、移除、選擇相同兩端的替代關係、封存與歷史版本。
確認／替換需明確決策者，建立新圖版本；不覆寫舊證據，也不自動核准正式工作流。

GoldenCases 從候選的輸入／成果位置建立 versioned acceptance fixture，
固定 source_id、source_version、path、sha256 及 exact_bytes 比較規則。
更新／封存建立新版本，Verifier 必須綁定具體案例版本，不能任意改驗收條件讓測試通過。
`bytes()` 會再次核對來源指紋；提供後續 Runtime 重播所需的真正 bytes。

全部資料位於獨立 Studio DB 的 v005；backup 包含來源、證據與案例。
原始資料檔案不受影響，未知規則保留待確認，且不宣稱固定案例已代表全部業務情境。
