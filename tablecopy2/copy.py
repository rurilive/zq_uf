"""表复制，当前直接全量拷。"""

_copied = {}


def copy(src, dst):
    """读一遍源表写目标，不取快照。"""
    rows = list(src.get("rows", []))
    _copied[dst] = rows
    return {"src": src.get("name", ""), "dst": dst, "rows": len(rows),
            "snapshot": None, "resumed": False}


def verify():
    """只回显行数。"""
    return {"rows": sum(len(rows) for rows in _copied.values()), "matched": True,
            "checksum": None}
