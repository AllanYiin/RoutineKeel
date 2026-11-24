# 0.5.0 驗證紀錄（2026-11-19）

Windows x64 本機完整字幕流程測試 **97 passed、0 skipped**。包含真實 noisereduce 非平穩降噪、極短音軌與脈衝位置、跨區塊預加重狀態、防削波、標準／參考模式與快取隔離，以及聽審音檔在片頭、片尾、短句與超過 30 秒來源段落的精確 PCM 裁切。另驗證多個疑義只採目前來源段落裁切，不採第一筆子片段疑義時間；頁面拒絕與目前段落不符的原音座標。保留一項既有 CTranslate2/pkg_resources 棄用警告。

從 repo 根目錄可重跑：

```sh
uv run --locked --project "workflows/FLOW_課程影片產生字幕/src/ai_flow01/profiles/demucs-cpu" --with "pytest==9.2.0" --with-editable flow_base --with-requirements "workflows/FLOW_課程影片產生字幕/tests/demucs-test-requirements.txt" python -m pytest "workflows/FLOW_課程影片產生字幕/tests" -q
python tools/release.py --project ai_flow01
```

測試依賴檔保留 ASR 測試所需版本；NumPy 與底座直接依賴由各自專案約束提供，避免 ASR 測試的間接依賴覆蓋 Demucs 固定 NumPy 或底座固定 filelock。模型權重不包含在測試依賴檔內。

正式 `catalog → plan → run` 入口實測同一段 4.220 秒原音的 standard 與 reference 模式，使用相同中文提示、large-v3 CUDA float16 及 Demucs CPU。兩次 5 個步驟皆為 executed；來源原音與辨識音軌均為 16 kHz 單聲道、67,520 個取樣點。快照 run_id、模式、提示、來源指紋及實際後端已核對。標準模式的人聲 WAV 與 0.4.1 的同段已保存產物逐位元組相同；參考模式確實使用 noisereduce 3.0.3 及係數 0.97 預加重。

兩次 SRT／VTT 各 1 則字幕，技能驗證器按 30 字元／行、2 行、0.5–12 秒、15 CPS 檢查，均為 0 error、0 warning；工作狀態仍為 REVIEW_REQUIRED。流程 LLM API calls/tokens 均為 0。這是執行與機械檢查證據：短片中的已知術語仍有辨識錯誤，沒有證明參考模式提升準確率，故 standard 仍為預設。沒有執行 WER/CER、全文聽審、全課程重辨識或其他 OS 的實體模型測試。

另以既有工作執行 review-build，保留人工文字修訂與原 ASR，重建確認頁、CSV、校對稿及分享 ZIP。172 份聽審 WAV 的實際 PCM、長度、取樣率及雜湊與 review_audio.json／原音核對一致；確認頁可顯示來源段落、前後文音檔與 SRT 的不同時間範圍。這不等同語音／文字同步驗收；既有硬性 QA 仍維持 FAIL，沒有自動核准。課程內容、原音及 outputs 不隨發行或版控散布。

# 0.4.1 驗證紀錄（2026-11-18）

Windows x64 以正式 catalog、plan、run 入口及預設 12 秒上限離線執行兩個案例。兩次均完成五步，使用 Demucs CPU 與 large-v3 CUDA。本流程原始碼測試共 78 項通過。

- 有停頓的合成英語錄音長 15.243 秒。辨識得到的 13.840 秒段落有三處內部停頓，最長為 5.984 到 8.096 秒，首次切點為 7.040 秒。為符合兩行容量，子段各自沿實測停頓分割。4 條 SRT 逐條比對已知口播稿，文字均位於正確的語句。1 個來源段列入聽審，0 項硬性問題。
- 另一段連續口播長 14.690 秒。13.540 秒的原始辨識段內沒有測得內部停頓，SRT 保留整段起訖和完整文字。聽審表說明保留原因，QA 如實回報 3 項硬性問題。
- 另驗證最長停頓偏離中央、遞迴分割、等長時的選擇、12 秒整、無可用音訊、字詞時間跨過停頓、人工改稿與無可用文字分界。切點紀錄、ZIP、SRT、VTT、確認表與聽審原音均已核對。

## 0.4.0 驗證紀錄（2026-11-18）

Windows x64 以 flow_base 0.2.12 的 catalog、plan、run 驗證 24.228 秒的合成立體聲英文錄音，內含兩段人聲、背景伴奏及底噪。最終離線執行的 5 步均為 executed。Demucs 2.6.0 CPU 權重推論及 faster-whisper large-v3 CUDA 後端均經實測。字幕流程原始碼測試套件 67 項通過，包含 3 秒整與多一個取樣點的行為、分塊首尾、正規化、舊版恢復與人工審閱。

原音與辨識人聲均為 387,646 個 16 kHz 取樣點。VAD 確認三段長靜音共 15.619875 秒，ASR 只接收原片 4.608–8.928 秒及 14.944–19.232 秒的處理後人聲。逐段比對 ASR 波形與處理後 WAV，並比對 3 個聽審片段與原音，取樣資料均相符。4 條 SRT 均落在語音區間，格式硬性問題 0，3 段待真人聽審。這筆測試的工作流 LLM 呼叫與 token 均為 0。字幕正確率與其他作業系統尚待獨立驗證。

## 0.3.4 驗證紀錄（2026-11-16）

本版只更新字幕流程；使用 flow_base 0.2.9。主交付是離線 review.html、中文疑義確認表 CSV、校對稿 TXT、SRT/VTT 與分享 ZIP。QA.json 保持機器契約。原文對照取自已保存的 recognizer_text，沒有新增 LLM API 呼叫或重新辨識來製作確認頁。

完整測試：`FLOW_BASE_TEST_PWSH=/path/to/pwsh uv run --project base --group dev pytest tests ai_flow01/tests -q`，**199 passed、0 skipped**。

新增驗證涵蓋：人類頁面與原音內嵌、較大音訊走相對路徑且 ZIP 包含原音、中文 CSV 與校對稿、CSV 填答匯入／重建、資料版本與原文核對、未知／重複片段拒絕、舊人工修訂與字幕版本保存、過期核准移至版本紀錄、不自動核准、無疑義仍需人審、HTML 文字轉義與 CSV 公式防護、隔離 Python -I 實際載入 worker／確認頁模組。匯入未修改資料即失敗時，保留既有報告；已修改而重建失敗時明示未完成。

initial_prompt 的程式預設與發行設定均為「繁體中文」，舊空白值啟用時補上預設，自訂提示保留，明確 initial_prompt_enabled=false 才停用。測試確認提示實際進入每段 model.transcribe 呼叫；歷史 0.3.2 空提示快照恢復仍以原設定校驗，不為新預設重新辨識。

以兩段明確標示為合成的文字與測試音建立交付頁，透過 jsdom 26.1.0 執行頁面本身的 JavaScript：填答、進度計數、未填答篩選、搜尋、帶逗號／換行／引號的 CSV 下載、暫存復原、儲存不可用時的提示均通過。該下載 CSV 再由 Python 匯入並重建 SRT，結果為 REVIEW_REQUIRED，未執行 ASR、未建立人工核准。

限制：jsdom 不是完整瀏覽器。本環境無法啟動完整 Chromium，因此未完成視覺截圖、瀏覽器原音播放與 Windows Edge／Chrome、macOS Safari 真機驗收。本輪未重辨識使用者課程，未將附件的歷史確認表當成本次新辨識成果；使用者附上的 QA 仍有硬性問題，UI 改善不等於品質驗收通過。
