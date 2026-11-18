"""Independent fixture oracle; never supplied to Discovery or used as a generated handler."""

import csv
import hashlib
import io
import json


def csv_bytes(rows, fields):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def json_bytes(value, **kwargs):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, **kwargs) + "\n").encode()


def normalize(data, strip):
    text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    if strip:
        text = "\n".join(line.strip() for line in text.splitlines())
    return (text.rstrip("\n") + "\n").encode()


def evaluate(rule, data):
    if rule == "copy_bytes":
        return data
    text = data.decode("utf-8-sig")
    if rule in {"normalize_trim", "normalize_keep"}:
        return normalize(data, rule == "normalize_trim")
    if rule in {"file_report", "normalize_report"}:
        content = normalize(data, True) if rule == "normalize_report" else data
        return json_bytes({"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    if rule == "pretty_json":
        return json_bytes(json.loads(text), indent=2)
    if rule == "unique_lines":
        values = dict.fromkeys(line.strip() for line in text.splitlines() if line.strip())
        return ("\n".join(values) + "\n").encode()
    if rule == "error_lines":
        return "".join(
            line[6:] + "\n" for line in text.splitlines() if line.startswith("ERROR|")
        ).encode()
    if rule == "replace_marker":
        return text.replace("[NAME]", "Guest").encode()
    if rule == "urgent_branch":
        return normalize(data, True) if "urgent" in text else data
    if rule in {"orders_filter", "inventory_columns", "line_totals", "queue_filter", "stock_flags"}:
        rows = list(csv.DictReader(io.StringIO(text)))
        if rule == "orders_filter":
            return csv_bytes(
                [
                    {"id": r["id"], "amount": r["amount"]}
                    for r in rows
                    if r["status"] == "open" and int(r["amount"]) >= 100
                ],
                ["id", "amount"],
            )
        if rule == "inventory_columns":
            return csv_bytes(
                [{"item": r["sku"], "quantity": r["stock"]} for r in rows], ["item", "quantity"]
            )
        if rule == "line_totals":
            return csv_bytes(
                [
                    {"item": r["item"], "total_cents": int(r["qty"]) * int(r["unit_cents"])}
                    for r in rows
                ],
                ["item", "total_cents"],
            )
        if rule == "queue_filter":
            return csv_bytes(
                [
                    {"id": r["id"]}
                    for r in rows
                    if r["status"] == "pending" and int(r["priority"]) >= 3
                ],
                ["id"],
            )
        return csv_bytes(
            [{"id": r["id"], "decision": "restock" if int(r["qty"]) < 5 else "ok"} for r in rows],
            ["id", "decision"],
        )
    value = json.loads(text)
    if rule == "category_sum":
        totals = {}
        for row in value:
            totals[row["category"]] = totals.get(row["category"], 0) + row["quantity"]
        return json_bytes(totals)
    if rule == "count_flags":
        return json_bytes(
            {
                "active": sum(row["active"] is True for row in value),
                "inactive": sum(row["active"] is False for row in value),
            }
        )
    if rule == "priority_names":
        return json_bytes(
            [row["name"] for row in sorted(value, key=lambda row: row["priority"], reverse=True)]
        )
    if rule == "join_scores":
        scores = {row["id"]: row["score"] for row in value["scores"]}
        return json_bytes(
            [
                {"name": row["name"], "score": scores[row["id"]]}
                for row in value["people"]
                if row["id"] in scores
            ]
        )
    if rule == "sort_tags":
        return ("\n".join(sorted(set(value))) + "\n").encode()
    raise AssertionError("Unknown independent fixture rule: " + rule)
