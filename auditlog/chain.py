"""审计日志链，当前每条记录只算自身摘要。"""

import hashlib
import json

_records = []


def _digest(record):
    payload = json.dumps(record, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def append(record):
    """追加到尾部，摘要只覆盖本条内容。"""
    if not isinstance(record, dict):
        raise ValueError("record must be a dict")
    digest = _digest(record)
    _records.append({
        "seq": len(_records),
        "day": record.get("day", ""),
        "record": dict(record),
        "digest": digest,
    })
    return {
        "seq": len(_records) - 1,
        "day": record.get("day", ""),
        "digest": digest,
        "link": "",
    }


def verify(span=None):
    """不看区间，每次从头重算一遍。"""
    if span is not None and not isinstance(span, (tuple, list, dict)):
        raise ValueError("span must be a tuple, list or dict")
    broken = None
    for item in _records:
        if _digest(item["record"]) != item["digest"]:
            broken = item["seq"]
            break
    return {
        "ok": broken is None,
        "checked": len(_records),
        "start": 0,
        "end": len(_records) - 1 if _records else None,
        "broken": broken,
        "link": "",
        "resume": None,
    }


def tail():
    """取尾部摘要，没有记录时返回空。"""
    if not _records:
        return {"seq": None, "day": "", "digest": ""}
    last = _records[-1]
    return {"seq": last["seq"], "day": last["day"], "digest": last["digest"]}
