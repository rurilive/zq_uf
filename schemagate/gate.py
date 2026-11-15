"""结构变更门禁，当前直接改表。"""

_applied = []


def check(old, new):
    """不做兼容判断，一律放行。"""
    return {"allowed": True, "breaking": [], "compatible": [], "audit": []}


def apply(plan):
    """直接应用全部表，没有灰度和回滚。"""
    tables = list(plan.get("tables", []))
    _applied.extend(tables)
    return {"applied": tables, "gray": [], "rolled_back": [], "audit": []}
