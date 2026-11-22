"""Author the fixed synthetic corpus, never run it as a model acceptance campaign."""

import hashlib
import json
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent / "fixtures/acceptance_v1"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n"


def report(value):
    data = value.encode("utf-8")
    return encoded({"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})


def definition(
    number,
    title,
    mode,
    rule,
    brief,
    inputs,
    expected,
    *,
    input_format="txt",
    output_format="txt",
    comparison="exact_bytes",
    diagram=None,
    intermediates=None,
    training_count=1,
):
    return {
        "id": f"C{number:02d}",
        "title": title,
        "mode": mode,
        "rule": rule,
        "brief": brief,
        "inputs": inputs,
        "expected": expected,
        "input_format": input_format,
        "output_format": output_format,
        "comparison": comparison,
        "diagram": diagram,
        "intermediates": intermediates,
        "training_count": training_count,
    }


def definitions():
    return [
        definition(
            1,
            "整理行首尾空白與換行",
            "text",
            "normalize_trim",
            "將 CRLF 或 CR 統一成 LF，去除每行頭尾空白，最後恰好一個 LF；保留行的順序。",
            ["  alpha \r\n beta  ", "\t新項目\t\r\n尾端", " one\r two \r\nthree\n\n"],
            ["alpha\nbeta\n", "新項目\n尾端\n", "one\ntwo\nthree\n"],
        ),
        definition(
            2,
            "篩選待處理訂單",
            "text",
            "orders_filter",
            "讀取 CSV，只保留 status 等於 open 且整數 amount 至少 100 的列。輸出 id,amount 兩欄，保留原順序及 CSV 引號語意，使用 LF。",
            [
                "id,amount,status\nA,150,open\nB,90,open\nC,200,closed\n",
                "id,amount,status\nH,100,open\nI,99,open\nJ,500,closed\n",
                'id,amount,status\n"Q,7",101,open\nR,0,open\nS,800,open\n',
            ],
            ["id,amount\nA,150\n", "id,amount\nH,100\n", 'id,amount\n"Q,7",101\nS,800\n'],
            input_format="csv",
            output_format="csv",
        ),
        definition(
            3,
            "按分類加總數量",
            "text",
            "category_sum",
            "JSON 陣列每筆有 category 與整數 quantity，依 category 加總，輸出分類到整數總數的 JSON 物件。",
            [
                encoded(
                    [
                        {"category": "A", "quantity": 2},
                        {"category": "B", "quantity": 3},
                        {"category": "A", "quantity": 4},
                    ]
                ),
                encoded(
                    [
                        {"category": "新", "quantity": 0},
                        {"category": "舊", "quantity": 7},
                        {"category": "新", "quantity": 5},
                    ]
                ),
                encoded([{"category": "Z", "quantity": 9}, {"category": "Z", "quantity": 1}]),
            ],
            [encoded({"A": 6, "B": 3}), encoded({"新": 5, "舊": 7}), encoded({"Z": 10})],
            input_format="json",
            output_format="json",
            comparison="json_equal",
        ),
        definition(
            4,
            "清理重複清單",
            "text",
            "unique_lines",
            "每行去頭尾空白，移除空行及重複項目，大小寫有別，保留第一次出現順序。輸出每項一行，最後 LF。",
            ["a\n a \n\nb\n", "紅\n藍\n紅\n藍色\n", " X\nx\nX\n Y \n"],
            ["a\nb\n", "紅\n藍\n藍色\n", "X\nx\nY\n"],
        ),
        definition(
            5,
            "統計啟用狀態",
            "text",
            "count_flags",
            "JSON 陣列的 active 欄位皆為布林值，計數 true 與 false，輸出 active 與 inactive 兩個整數欄位。",
            [
                encoded([{"active": True}, {"active": False}, {"active": True}]),
                encoded([{"active": False}, {"active": False}]),
                encoded([{"active": True}]),
            ],
            [
                encoded({"active": 2, "inactive": 1}),
                encoded({"active": 0, "inactive": 2}),
                encoded({"active": 1, "inactive": 0}),
            ],
            input_format="json",
            output_format="json",
            comparison="json_equal",
        ),
        definition(
            6,
            "重整庫存欄位",
            "mermaid",
            "inventory_columns",
            "讀取 CSV，選 sku 與 stock 欄，輸出欄名改為 item,quantity；保留字串值與順序，不輸出 note，CSV 使用 LF。",
            [
                "sku,stock,note\nP1,4,first\nP2,10,second\n",
                "sku,stock,note\nH1,0,empty\nH2,7,keep\n",
                'sku,stock,note\n"Q,1",12,"with, comma"\n',
            ],
            [
                "item,quantity\nP1,4\nP2,10\n",
                "item,quantity\nH1,0\nH2,7\n",
                'item,quantity\n"Q,1",12\n',
            ],
            input_format="csv",
            output_format="csv",
            diagram="選 sku 與 stock 並改名 item 與 quantity",
        ),
        definition(
            7,
            "計算各列小計",
            "mermaid",
            "line_totals",
            "CSV 的 qty 與 unit_cents 為非負整數。每列 total_cents 等於兩者相乘；輸出 item,total_cents，保留列順序，金額使用整數分，不做浮點計算。",
            [
                "item,qty,unit_cents\nchair,2,125\ntable,1,900\n",
                "item,qty,unit_cents\ncable,3,50\nempty,0,999\n",
                "item,qty,unit_cents\nlabel,7,11\n",
            ],
            [
                "item,total_cents\nchair,250\ntable,900\n",
                "item,total_cents\ncable,150\nempty,0\n",
                "item,total_cents\nlabel,77\n",
            ],
            input_format="csv",
            output_format="csv",
            diagram="整數數量乘單價分並輸出小計",
        ),
        definition(
            8,
            "依優先順序排列工作",
            "mermaid",
            "priority_names",
            "JSON 陣列每筆有 name 與整數 priority。priority 由大到小排序，同分維持原順序；輸出 name 的 JSON 陣列。",
            [
                encoded(
                    [
                        {"name": "a", "priority": 1},
                        {"name": "b", "priority": 3},
                        {"name": "c", "priority": 1},
                    ]
                ),
                encoded(
                    [
                        {"name": "first", "priority": 0},
                        {"name": "second", "priority": 0},
                        {"name": "low", "priority": -1},
                    ]
                ),
                encoded(
                    [
                        {"name": "x", "priority": 2},
                        {"name": "y", "priority": 8},
                        {"name": "z", "priority": 5},
                    ]
                ),
            ],
            [
                encoded(["b", "a", "c"]),
                encoded(["first", "second", "low"]),
                encoded(["y", "z", "x"]),
            ],
            input_format="json",
            output_format="json",
            comparison="json_equal",
            diagram="優先度降冪同分原順序再取名稱",
        ),
        definition(
            9,
            "以識別碼合併兩組資料",
            "mermaid",
            "join_scores",
            "一份 JSON 含 people 與 scores 兩個陣列，各組 id 唯一。以 id 內連接，只輸出有分數的人的 name,score，依 people 原順序，輸出 JSON 陣列。",
            [
                encoded(
                    {
                        "people": [{"id": "a", "name": "甲"}, {"id": "b", "name": "乙"}],
                        "scores": [{"id": "b", "score": 5}, {"id": "a", "score": 7}],
                    }
                ),
                encoded(
                    {
                        "people": [{"id": "x", "name": "X"}, {"id": "y", "name": "Y"}],
                        "scores": [{"id": "y", "score": 10}],
                    }
                ),
                encoded(
                    {"people": [{"id": "k", "name": "K"}], "scores": [{"id": "other", "score": 99}]}
                ),
            ],
            [
                encoded([{"name": "甲", "score": 7}, {"name": "乙", "score": 5}]),
                encoded([{"name": "Y", "score": 10}]),
                encoded([]),
            ],
            input_format="json",
            output_format="json",
            comparison="json_equal",
            diagram="以 id 內連接並依人員順序輸出姓名分數",
        ),
        definition(
            10,
            "擷取錯誤紀錄",
            "mermaid",
            "error_lines",
            "只保留行首精確等於 ERROR| 的文字列，去掉這個前綴，其餘字元保留，包含訊息中的其他 |。不去頭尾空白，不比對小寫 error；每筆輸出 LF，沒有符合項目時輸出空檔。",
            [
                "INFO|ok\nERROR|a|b\nERROR|c\n",
                " ERROR|ignore\nerror|ignore\nERROR|kept\n",
                "INFO|clean\r\nWARN|minor\r\n",
            ],
            ["a|b\nc\n", "kept\n", ""],
            diagram="篩 ERROR 前綴並擷取其後完整訊息",
        ),
        definition(
            11,
            "原封不動複製檔案",
            "folder",
            "copy_bytes",
            "參考成功資料夾，輸出必須與輸入逐位元組相同，不改空白、BOM 或換行。",
            [" keep me \r\n", "  新樣本 \r\nX", "\ufeffKEEP\r\n"],
            [" keep me \r\n", "  新樣本 \r\nX", "\ufeffKEEP\r\n"],
        ),
        definition(
            12,
            "標準化 JSON 排版",
            "folder",
            "pretty_json",
            "JSON 鍵名遞迴排序，兩個空白縮排，中文字直接保留，最後一個 LF。內容不改動，排版也要完全相同。",
            ['{"b":2,"a":1}', '{"z":{"b":0,"a":"中"},"a":[2,1]}', '{"c":true,"b":null,"a":[] }'],
            [
                '{\n  "a": 1,\n  "b": 2\n}\n',
                '{\n  "a": [\n    2,\n    1\n  ],\n  "z": {\n    "a": "中",\n    "b": 0\n  }\n}\n',
                '{\n  "a": [],\n  "b": null,\n  "c": true\n}\n',
            ],
            input_format="json",
            output_format="json",
        ),
        definition(
            13,
            "只整理換行而保留空白",
            "folder",
            "normalize_keep",
            "CRLF 與 CR 改為 LF，保留每行頭尾空白，結尾移除多餘 LF 後保留恰好一個 LF。",
            [" a \r\n b ", " x \r y \r\n\r\n", "\tkeep\t\r\nlast "],
            [" a \n b \n", " x \n y \n", "\tkeep\t\nlast \n"],
        ),
        definition(
            14,
            "產生檔案指紋清單",
            "folder",
            "file_report",
            "輸出 JSON 包含輸入檔案的位元組數 bytes 與完整 SHA-256 sha256；不是字元數。",
            ["abc", "你好", "row-2\n"],
            [report("abc"), report("你好"), report("row-2\n")],
            output_format="json",
            comparison="json_equal",
        ),
        definition(
            15,
            "先清理文字再產生指紋",
            "folder",
            "normalize_report",
            "參考原始檔、中間成果與最終成果：先統一 LF 並去每行頭尾空白、結尾一個 LF，再對清理後 bytes 產生 bytes 與 SHA-256 JSON。",
            ["  alpha \r\n beta  ", "  中文 \r\n 清單 ", " x\r y "],
            [report("alpha\nbeta\n"), report("中文\n清單\n"), report("x\ny\n")],
            output_format="json",
            comparison="json_equal",
            intermediates=["alpha\nbeta\n"],
        ),
        definition(
            16,
            "依圖替換文字標記",
            "image",
            "replace_marker",
            "依附圖的文字處理規則轉換，保留沒有被規則指定修改的字元。看不清楚請提問。",
            ["Hi [NAME]\n", "Bye [NAME] + [NAME]\n", "No marker here\r\n"],
            ["Hi Guest\n", "Bye Guest + Guest\n", "No marker here\r\n"],
            diagram="Replace EVERY literal [NAME] with Guest. Keep all other characters.",
        ),
        definition(
            17,
            "依圖篩選佇列",
            "image",
            "queue_filter",
            "依附圖篩選 CSV 並輸出指定欄位，保留原順序，使用 LF。看不清楚請提問。",
            [
                "id,priority,status\nA,3,pending\nB,4,done\nC,2,pending\n",
                "id,priority,status\nH,2,pending\nI,5,pending\n",
                "id,priority,status\nX,3,done\nY,3,pending\n",
            ],
            ["id\nA\n", "id\nI\n", "id\nY\n"],
            input_format="csv",
            output_format="csv",
            diagram="Keep rows: status = pending AND integer priority >= 3. Output only id.",
        ),
        definition(
            18,
            "依圖選擇文字處理路徑",
            "image",
            "urgent_branch",
            "依附圖的條件與兩條路徑處理文字。請保留真正的條件判斷；看不清楚請提問。",
            [
                " urgent \r\n ticket ",
                " normal \r\n ticket ",
                "new urgent task\r\n detail ",
                " URGENT \r\nraw ",
            ],
            [
                "urgent\nticket\n",
                " normal \r\n ticket ",
                "new urgent task\ndetail\n",
                " URGENT \r\nraw ",
            ],
            diagram="branch",
            training_count=2,
        ),
        definition(
            19,
            "依圖整理標籤",
            "image",
            "sort_tags",
            "依附圖將 JSON 字串陣列轉為文字清單。字串皆為小寫英文字；看不清楚請提問。",
            [
                encoded(["pear", "apple", "pear"]),
                encoded(["kiwi", "fig", "apple", "fig"]),
                encoded(["zebra", "ant", "zebra", "bee"]),
            ],
            ["apple\npear\n", "apple\nfig\nkiwi\n", "ant\nbee\nzebra\n"],
            input_format="json",
            diagram="Remove duplicate tags. Sort alphabetically A-Z. Write one tag per LF line, final LF.",
        ),
        definition(
            20,
            "依圖標記補貨需求",
            "image",
            "stock_flags",
            "依附圖為 CSV 的每筆資料產生標記，只輸出檔案，不執行採購或通知。看不清楚請提問。",
            ["id,qty\nA,0\nB,5\n", "id,qty\nH,4\nI,100\nJ,5\n", "id,qty\nX,1\nY,6\n"],
            [
                "id,decision\nA,restock\nB,ok\n",
                "id,decision\nH,restock\nI,ok\nJ,ok\n",
                "id,decision\nX,restock\nY,ok\n",
            ],
            input_format="csv",
            output_format="csv",
            diagram="For EACH row: integer qty < 5 -> restock; otherwise -> ok. Output id,decision. Keep order.",
        ),
    ]


def draw_diagram(path, rule):
    image = Image.new("RGB", (1000, 620), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=22)

    def box(bounds, text):
        draw.rectangle(bounds, outline="black", width=3)
        draw.multiline_text(
            (bounds[0] + 15, bounds[1] + 15),
            textwrap.fill(text, width=max(15, (bounds[2] - bounds[0] - 30) // 13)),
            font=font,
            fill="black",
            spacing=6,
        )

    def arrow(start, end):
        draw.line((start, end), fill="black", width=3)
        x, y = end
        draw.polygon([(x, y), (x - 7, y - 13), (x + 7, y - 13)], fill="black")

    box((340, 20, 660, 90), "INPUT FILE")
    if rule == "branch":
        box((200, 140, 800, 250), 'Contains literal lowercase "urgent"?')
        arrow((500, 90), (500, 140))
        draw.line((320, 250, 210, 280), fill="black", width=3)
        draw.line((680, 250, 790, 280), fill="black", width=3)
        arrow((210, 280), (210, 310))
        arrow((790, 280), (790, 310))
        draw.text((230, 280), "YES", font=font, fill="black")
        draw.text((740, 280), "NO", font=font, fill="black")
        box((30, 310, 470, 440), "Normalize CRLF/CR to LF. Trim each line. Exactly one final LF.")
        box((530, 310, 970, 440), "COPY BYTES UNCHANGED")
        draw.line((250, 440, 500, 510, 750, 440), fill="black", width=3)
        arrow((500, 510), (500, 540))
    else:
        box((90, 160, 910, 420), rule)
        arrow((500, 90), (500, 160))
        arrow((500, 420), (500, 540))
    box((340, 540, 660, 610), "OUTPUT FILE")
    image.save(path, format="PNG")


def build():
    ROOT.mkdir(parents=True, exist_ok=True)
    catalog = {
        "schema_version": 1,
        "dataset_version": "acceptance-v1",
        "status": "fixtures_only_not_acceptance_results",
        "cases": [],
    }
    for definition in definitions():
        identity = definition["id"]
        folder = ROOT / identity
        authoring = folder / "authoring"
        authoring.mkdir(parents=True, exist_ok=True)
        training = folder / "training"
        goal = {
            "description": definition["brief"],
            "success_criteria": ["符合來源規則與固定驗收案例；不修改輸入檔，不執行外部副作用。"],
        }
        brief = (
            definition["title"]
            + "\n\n"
            + definition["brief"]
            + "\n\n輸出格式："
            + definition["output_format"]
            + "\n"
        )
        (authoring / "brief.txt").write_bytes(brief.encode())
        if definition["mode"] == "mermaid":
            (authoring / "flow.mmd").write_text(
                "flowchart TD\nA[輸入檔案] --> B["
                + definition["diagram"]
                + "]\nB --> C[輸出結果]\n",
                encoding="utf-8",
                newline="\n",
            )
        if definition["mode"] == "image":
            draw_diagram(authoring / "diagram.png", definition["diagram"])
        case = {
            "id": identity,
            "title": definition["title"],
            "mode": definition["mode"],
            "goal": goal,
            "evaluator_rule": definition["rule"],
            "comparison": definition["comparison"],
            "visible_sources": [f"{identity}/authoring", f"{identity}/training"],
            "training": [],
            "held_out": [],
            "acceptance_status": "not_run",
            "files": {},
        }
        for index, (source, expected) in enumerate(
            zip(definition["inputs"], definition["expected"], strict=True)
        ):
            split = "training" if index < definition["training_count"] else "held_out"
            number = index + 1 if split == "training" else index - definition["training_count"] + 1
            input_path = folder / split / "input" / f"sample{number}.{definition['input_format']}"
            expected_path = (
                folder / split / "output" / f"result{number}.{definition['output_format']}"
            )
            for path, content in ((input_path, source), (expected_path, expected)):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content.encode("utf-8"))
            case[split].append(
                {
                    "input": input_path.relative_to(ROOT).as_posix(),
                    "expected": expected_path.relative_to(ROOT).as_posix(),
                }
            )
        if definition["intermediates"]:
            intermediate = training / "intermediate/normalized.txt"
            intermediate.parent.mkdir(parents=True, exist_ok=True)
            intermediate.write_bytes(definition["intermediates"][0].encode())
        for path in sorted(folder.rglob("*")):
            if path.is_file():
                case["files"][path.relative_to(ROOT).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        catalog["cases"].append(case)
    catalog_bytes = (json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode()
    (ROOT / "catalog.json").write_bytes(catalog_bytes)
    template = {
        "dataset_version": "acceptance-v1",
        "dataset_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "required_independent_successes": 16,
        "cases": [
            {
                "case_id": c["id"],
                "status": "not_run",
                "participant_id": None,
                "nontechnical_participant": None,
                "manual_code_or_config_edits": None,
                "held_out_exposed_to_model": None,
                "model_identity": None,
                "project_id": None,
                "revision_id": None,
                "design_call_ids": [],
                "design_cost_micro_usd": None,
                "provider_usage_complete": None,
                "registered_runtime_requests": None,
                "training_report_ids": [],
                "held_out_report_ids": [],
                "package_hash": None,
                "release_id": None,
                "elapsed_seconds": None,
                "reviewer_notes": "",
            }
            for c in catalog["cases"]
        ],
    }
    (ROOT / "results-template.json").write_bytes(
        (json.dumps(template, ensure_ascii=False, indent=2) + "\n").encode()
    )


if __name__ == "__main__":
    build()
