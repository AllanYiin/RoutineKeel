# 0.3.4 驗證紀錄（2026-11-16）

本版只更新字幕流程；使用 flow_base 0.2.9。主交付是離線 review.html、中文疑義確認表 CSV、校對稿 TXT、SRT/VTT 與分享 ZIP。QA.json 保持機器契約。原文對照取自已保存的 recognizer_text，沒有新增 LLM API 呼叫或重新辨識來製作確認頁。

完整測試：`FLOW_BASE_TEST_PWSH=/path/to/pwsh uv run --project base --group dev pytest tests ai_flow01/tests -q`，**199 passed、0 skipped**。

新增驗證涵蓋：人類頁面與原音內嵌、較大音訊走相對路徑且 ZIP 包含原音、中文 CSV 與校對稿、CSV 填答匯入／重建、資料版本與原文核對、未知／重複片段拒絕、舊人工修訂與字幕版本保存、過期核准移至版本紀錄、不自動核准、無疑義仍需人審、HTML 文字轉義與 CSV 公式防護、隔離 Python -I 實際載入 worker／確認頁模組。匯入未修改資料即失敗時，保留既有報告；已修改而重建失敗時明示未完成。

initial_prompt 的程式預設與發行設定均為「繁體中文」，舊空白值啟用時補上預設，自訂提示保留，明確 initial_prompt_enabled=false 才停用。測試確認提示實際進入每段 model.transcribe 呼叫；歷史 0.3.2 空提示快照恢復仍以原設定校驗，不為新預設重新辨識。

以兩段明確標示為合成的文字與測試音建立交付頁，透過 jsdom 26.1.0 執行頁面本身的 JavaScript：填答、進度計數、未填答篩選、搜尋、帶逗號／換行／引號的 CSV 下載、暫存復原、儲存不可用時的提示均通過。該下載 CSV 再由 Python 匯入並重建 SRT，結果為 REVIEW_REQUIRED，未執行 ASR、未建立人工核准。

限制：jsdom 不是完整瀏覽器。本環境無法啟動完整 Chromium，因此未完成視覺截圖、瀏覽器原音播放與 Windows Edge／Chrome、macOS Safari 真機驗收。本輪未重辨識使用者課程，未將附件的歷史確認表當成本次新辨識成果；使用者附上的 QA 仍有硬性問題，UI 改善不等於品質驗收通過。
