"""快照与变更对齐，当前各更各的。"""


def snapshot(table):
    """全量快照，不记位点。"""
    rows = list(table.get("rows", []))
    return {"rows": rows, "position": None, "cached": []}


def apply(events):
    """变更直接写入，不缓存也不校验。"""
    return {"applied": len(list(events)), "cached": 0, "position": None, "ok": True}
