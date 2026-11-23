"""Describe the host-led interview without opening a window or calling a model."""

LAYERS = [
    (0, "一句任務", "請用一句話描述想完成的任務。"),
    (1, "釐清", "由宿主草擬必要的是非題與選擇題，收取作者實際回答。"),
    (2, "工作流初稿", "展示只含工作流自身步驟的第一張流程圖，再逐步確認輸入、輸出與分支。"),
    (3, "輸入輸出", "確認整體及每一步的輸入輸出，包含本次製作重點與方向提醒。"),
    (4, "技能、工具與詞彙", "詢問內嵌技能與對應步驟、工具配置，整理專業詞彙表。"),
    (5, "檢核清冊", "逐項確認機器檢核、人類審核或不檢查。"),
    (6, "審核方式", "確認審核者、審核位置及可接受的完成條件。"),
    (7, "流程變數與研究", "區分固定值、可調設定、每次輸入與派生值；上網核對超參數及門檻的建議。"),
    (
        8,
        "定稿與藍圖包",
        "展示底座輸入檢核、交付及自訂步驟，逐項顯示所有預填內容，確認定稿後才匯出。",
    ),
]


def guide():
    return {
        "mode": "host_conversation",
        "next_question": LAYERS[0][2],
        "layers": [{"layer": i, "title": title, "instruction": text} for i, title, text in LAYERS],
        "model_calls": 0,
        "native_window": False,
        "confirmation": "只有作者實際回答才代表確認；未答不視為同意。",
        "interview": "層次是設計檢查清單。將獨立決策集中提問，沿用已有答案，最後確認摘要。",
        "persistence": "宿主可用 next_batch 與 reply_batch 在一次對話回覆保存多項決策。",
    }
