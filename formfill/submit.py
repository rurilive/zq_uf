"""批量填报的串行实现。"""
from dataclasses import dataclass
from datetime import date


@dataclass
class Receipt:
    business_key: str
    status: str
    message: str = ""


def _parse_date(value):
    year, month, day = [int(part) for part in str(value).split("-")]
    return date(year, month, day)


def normalize_row(row):
    return {
        "key": str(row["id"]),
        "date": _parse_date(row["date"]).isoformat(),
        "amount": round(float(row["amount"]), 2),
        "note": row.get("note", ""),
    }


def submit_batch(rows, submit_one=None):
    submit_one = submit_one or (lambda row: {"status": "ok"})
    receipts = []
    for row in rows:
        normalized = normalize_row(row)
        result = submit_one(normalized) or {}
        receipts.append(
            Receipt(normalized["key"], result.get("status", "ok"), result.get("message", ""))
        )
    return receipts
