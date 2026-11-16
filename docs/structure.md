# 目錄與協作方式

`routinekeel/` 收納產品原始碼，`routinekeel/flow_base/` 放共用底座。`routinekeel/flows/` 每個子目錄是一個可獨立發行的流程，存放該流程的程式、模板、相依宣告與測試。目錄名稱直接對應 ZIP 解壓後的名稱，Python 匯入名稱留在各元件的 `src/` 內。

| 來源 | Release 附件 | 解壓後的資料夾 |
| --- | --- | --- |
| `routinekeel/flow_base/` | `flow_base-<版本>.zip` | `flow_base/` |
| `routinekeel/flows/課程影片產生字幕/` | `flow_subtitles-<版本>.zip` | `課程影片產生字幕/` |
| `routinekeel/flows/檔案複製範例/` | `flow_copy-<版本>.zip` | `檔案複製範例/` |

GitHub Releases 提供上表的可執行發行包。各 zip 內有啟動器、wheel、文件及對應元件的原始碼快照。平常維護以儲存庫內的原始碼為準。測試用安裝目錄與本機建置結果都在 `dist/`。

學員建立 `RoutineKeel/` 後，把所需 ZIP 解壓到同一層。底座偵測環境與提供通用服務，流程選擇模型和後端、執行領域步驟並解讀結果。不同流程可以共用同一套底座。各流程的 `outputs/`、共用環境、快取與日誌由執行時建立。
