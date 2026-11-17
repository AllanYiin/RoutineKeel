CSV_FILTER = """def transform(text, params):
    rows = csv_read(text)
    selected = []
    for row in rows:
        if float(row["amount"]) >= params["minimum"] and row["status"] == "open":
            selected.append({"id": row["id"], "amount": row["amount"]})
    return csv_write(selected, ["id", "amount"])
"""

JSON_TOTAL = """def transform(text, params):
    rows = json_loads(text)
    totals = {}
    for row in rows:
        category = row["category"]
        totals[category] = totals.get(category, 0) + row["quantity"]
    return json_dumps(totals)
"""


def csv_parameters():
    return {
        "purpose": "篩選未結帳且金額達門檻的訂單",
        "program": CSV_FILTER,
        "arguments": {"minimum": 100},
        "argument_types": {"minimum": "number"},
        "tests": [
            {
                "input": "id,amount,status\nA,120,open\nB,90,open\n",
                "expected": "id,amount\nA,120\n",
                "arguments": {"minimum": 100},
            },
            {
                "input": "id,amount,status\nC,500,closed\nD,40,open\n",
                "expected": "id,amount\nD,40\n",
                "arguments": {"minimum": 20},
            },
        ],
    }


def json_parameters():
    return {
        "purpose": "按類別彙總數量",
        "program": JSON_TOTAL,
        "arguments": {},
        "tests": [
            {
                "input": '[{"category":"A","quantity":2},{"category":"A","quantity":3}]',
                "expected": '{"A": 5}',
            },
            {"input": '[{"category":"B","quantity":4}]', "expected": '{"B": 4}'},
        ],
    }
