"""Describe the host-led interview without opening a window or calling a model."""

LAYERS = [
    (0, "一句任務", "請用一句話描述想完成的任務。"),
    (1, "釐清", "由宿主草擬必要的是非題與選擇題，收取作者實際回答。"),
    (2, "步驟骨架", "草擬依序執行的步驟與流程圖，請作者修正。"),
    (3, "輸入輸出", "確認整體及每一步的輸入輸出，包含本次製作重點與方向提醒。"),
    (4, "工具清冊", "按工具彙整使用步驟、技能、依賴與缺口，交作者修正。"),
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
        "persistence": "此指令提供引導資料；逐層答案由宿主保存，不宣稱已有自動訪談狀態服務。",
    }
