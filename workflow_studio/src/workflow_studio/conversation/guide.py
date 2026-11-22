"""Describe the host-led interview without opening a window or calling a model."""

LAYERS = [
    (0, "一句任務", "請用一句話描述想完成的任務。"),
    (1, "釐清", "由宿主草擬必要的是非題與選擇題，收取作者實際回答。"),
    (2, "步驟骨架", "依現有需求草擬流程，和輸入、交付及校閱方式一起確認。"),
    (3, "輸入輸出", "確認整體及每一步的輸入輸出，包含本次製作重點與方向提醒。"),
    (4, "工具清冊", "預檢本機可用工具，列出能力、參數或權限缺口。"),
    (5, "檢核清冊", "逐項確認機器檢核、人類審核或不檢查。"),
    (6, "審核方式", "確認審核者、審核位置及可接受的完成條件。"),
    (7, "任務設定檔", "區分每次輸入、設定一次與 Agent 當下判斷並留理由的變數。"),
    (8, "藍圖包", "宿主整理已確認的設計與完整技能，交由底座另行試做及安裝。"),
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
