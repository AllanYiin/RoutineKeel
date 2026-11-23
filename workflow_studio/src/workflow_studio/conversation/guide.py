"""Describe the host-led interview without opening a window or calling a model."""

LAYERS = [
    (0, "一句任務", "請用一句話描述想完成的任務。"),
    (1, "主動草擬", "依目標預填前置條件、各步輸入輸出、正常分支、失敗、重試與驗收，供作者刪改。"),
    (2, "工作流初稿", "在對話中展示只含工作流自身步驟及分支的第一張圖，校閱後轉交該版的真人回覆。"),
    (3, "輸入輸出", "確認整體及每一步的輸入輸出，包含本次製作重點與方向提醒。"),
    (4, "技能、工具與詞彙", "詢問內嵌技能與對應步驟、工具配置，整理專業詞彙表。"),
    (5, "檢核清冊", "逐項確認機器檢核、人類審核或不檢查。"),
    (6, "審核方式", "確認審核者、審核位置及可接受的完成條件。"),
    (7, "流程變數與研究", "區分固定值、可調設定、每次輸入與派生值；上網核對超參數及門檻的建議。"),
    (
        8,
        "定稿與藍圖包",
        "在對話中顯示第二張圖，標出底座檢核、交付及自訂步驟。收到定稿的另一次真人確認才匯出。",
    ),
]


def guide():
    return {
        "mode": "host_conversation",
        "next_question": LAYERS[0][2],
        "layers": [{"layer": i, "title": title, "instruction": text} for i, title, text in LAYERS],
        "model_calls": 0,
        "native_window": False,
        "confirmation": "初稿及定稿各用綁定當前版本的實際使用者回覆。既有案例與 DEVNOTE 可供規劃。",
        "interview": "把所有預填項分組展示並請使用者校閱。沿用已答覆的內容，可各組一起確認或指出刪改。",
        "persistence": "宿主可用 next_batch 與 reply_batch 在一次對話回覆保存多項決策。",
    }
