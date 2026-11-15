"""变更批处理，当前逐条应用。"""

_applied = {}


def apply(batch):
    """逐条写，失败保留已写行。"""
    rows = list(batch.get("rows", []))
    done = 0
    for row in rows:
        if row.get("bad"):
            break
        done += 1
    _applied[batch.get("id", "")] = done
    return {"batch": batch.get("id", ""), "applied": done,
            "rolled_back": False, "skipped": 0, "conflicts": []}


def resume(token):
    """重放整批。"""
    return {"token": token, "resumed": True, "skipped": 0, "applied": _applied.get(token, 0)}
