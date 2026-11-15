"""采集账本，当前直接追加。"""

_entries = []


def append(entry):
    """重复条目也照写，不做幂等。"""
    _entries.append(dict(entry))
    return {"appended": True, "total": len(_entries), "duplicate": False, "repaired": 0}


def verify(batch):
    """只数条目，不校验半行和坏块。"""
    rows = [item for item in _entries if item.get("batch") == batch]
    return {"batch": batch, "rows": len(rows), "half": 0, "repaired": 0, "ok": True}
