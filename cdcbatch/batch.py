"""变更批处理，当前逐条应用。"""

_seen = {}


def apply(batch):
    """逐条写入，中途失败已写的行不回滚。"""
    rows = list(batch.get("rows", []))
    applied = 0
    for row in rows:
        if row.get("bad"):
            return {
                "batch": batch.get("id", ""),
                "applied": applied,
                "rolled_back": False,
                "skipped": 0,
                "conflicts": [],
            }
        applied += 1
    _seen[batch.get("id", "")] = applied
    return {
        "batch": batch.get("id", ""),
        "applied": applied,
        "rolled_back": False,
        "skipped": 0,
        "conflicts": [],
    }


def resume(token):
    """按令牌重放整批，不跳已应用的行。"""
    return {"token": token, "resumed": True, "skipped": 0, "applied": _seen.get(token, 0)}
