"""行级修复，当前一条坏行整批失败。"""

_batches = {}


def ingest(rows):
    """遇到坏行整批回退，不保留原文。"""
    good, bad = [], []
    for row in rows:
        if row.get("ok", True):
            good.append(row)
        else:
            bad.append(row)
    if bad:
        return {"batch": None, "stored": [], "isolated": [], "rolled_back": True}
    _batches["last"] = list(good)
    return {"batch": "last", "stored": good, "isolated": [], "rolled_back": False}


def replay(batch):
    """重放整批，已入库的会再写一次。"""
    rows = _batches.get(batch, [])
    return {"batch": batch, "replayed": len(rows), "matched": True}
