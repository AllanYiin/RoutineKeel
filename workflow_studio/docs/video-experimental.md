# 影片證據（P2 實驗功能，預設停用）

VideoIngestion 與 VideoDiscovery 預設 `enabled=False`。只有明確啟用的開發／測試環境可使用；
尚未通過真實視覺模型對實際操作影片的獨立驗收，不列為正式產品能力。

本機預覽支援 MP4／MOV／MKV／WebM，需現有 FFmpeg／ffprobe。
限制 100 MiB、10 分鐘、2000 萬像素，預設取五個代表影格（最多二十個）。
影片輸入與 JPEG 輸出經 pipe，FFmpeg protocol whitelist 只允許 pipe；
不將影片中的路徑、網址、畫面文字當成執行權限，不連接遠端媒體。
ffprobe／抽格有逾時限制；所有接受的代表影格經 Pillow 解碼，保留原始比例。
原片、時間軸、影格與來源版本保存於 Studio，來源移除不刪原始檔。

模型分析只發送明確選取的代表影格、時間資訊與工作目標，不發送原片音訊。
其他文字／資料夾證據只有被 context_source_ids 明確選取才加入範圍，且標示預覽截斷。
prepare 提供目的端／模型／資料範圍與 request_hash，對外傳送必須確認同一版本。
結果逐段輸出，完成後把 frame_index 映射到真正來源時間與影格位置。
未提供的影格、verified 宣告、不完整串流均拒絕提交；有效結果仍保存為實驗草稿。
缺少錄影片段保留 unknowns；多來源互斥 claim 由 evidence_fusion 保留 conflicted，不自行選邊。

本機固定測試影片為五影格合成測試圖，不是真人操作驗收素材。
測試證明實際抽格、時間／比例／保存、候選結構與拒絕行為；
模型候選使用 fixture，不能當成模型確實理解五個操作步驟的證據。
因此維持預設停用；停用不影響文字、Mermaid 與資料夾核心功能。
