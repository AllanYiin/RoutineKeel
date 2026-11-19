# 來源與移植範圍

主要參考為使用者提供 portable-srt-0.1.0.zip 的 cli.py；SHA-256：`a08aa998372934dead0cf8a2e7c13f9dcca6a4785daa33a8dafecde4da2074fc`。

portable_core.py 沿用其文字正規化、排版、QA、疑義篩選、遮罩及 20 段／3000 字元 packet 上限、HTML／CSV、人工核准及版本備份邏輯。改動：原音片段由既有 PCM WAV 讀取，無需額外 ffmpeg；校訂後清除過期字級對齊；缺原音片段列為 FAIL；核准前重跑 QA；再次強制零 LLM 與 packet 上限。

VAD／ASR 由隔離 worker 執行，沿用 portable-srt 的 VAD 預設及逐區段原時間偏移，增加完整性檢查與區段 checkpoint。安裝改由 flow_base 的鎖定環境與固定模型來源處理，沒有移植使用者特定 D 槽路徑。

subtitles.zip 為課程工作成果參考；其中人工 overrides、專題 prompt、DTW 強制對齊與課程內容不分發給新學員，不自動套用其他影片。此發行版不宣稱已實作那套 DTW 強制對齊。

另保留明確關閉繁體轉換的設定；疑義未改變時保留 CSV 人工註記，疑義表變更前保存版本。這些調整都在字幕包內，不移入 flow_base。

0.4.0 的人聲分離採用 torchaudio 2.6.0 的 HDEMUCS_HIGH_MUSDB_PLUS。流程從 PyTorch 官方位置取得權重並核對實測 SHA-256 `a004b2790d73ffeaa535db458a1a79b539dfdbafbccc31f275d07e632ebd7816`。分塊推論保留 vocals，重疊區間以互補權重相加。[模型定義](https://raw.githubusercontent.com/pytorch/audio/v2.6.0/src/torchaudio/pipelines/_source_separation_pipeline.py) · [官方權重](https://download.pytorch.org/torchaudio/models/hdemucs_high_trained.pt)

0.5.0 參考使用者提供的 Python 前處理程式，新增明確選用的 reference 模式，使用 noisereduce 3.0.3 的非平穩降噪及係數 0.97 的高頻預加重；standard 預設仍沿用 0.4.1。沿用本流程既有 Demucs 模型、44.1 kHz 立體聲模型輸入、原片取樣點座標、分塊交叉淡化及防削波正規化。預加重以 NumPy 計算並跨區塊保存前一個樣本，不新增 librosa。未採用參考程式中捕捉錯誤後靜默回傳原音的處理；前處理失敗仍中止。

noisereduce API 的 `y_noise` 僅用於平穩降噪；本次 `stationary=False` 沒有把錄音開頭 0.5 秒假定為純噪音。[noisereduce 原始碼](https://github.com/timsainb/noisereduce/blob/master/noisereduce/noisereduce.py)。保留 Demucs 仍須負擔其模型執行環境；官方 Demucs CLI 也宣告 torch 與 torchaudio 依賴：[最小依賴](https://github.com/facebookresearch/demucs/blob/main/requirements_minimal.txt)。
