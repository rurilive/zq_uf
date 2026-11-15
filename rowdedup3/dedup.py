"""数据行去重，当前后到覆盖。"""

_state = {}


def merge(rows):
    """同主键直接覆盖。"""
    for row in rows:
        _state[row.get("key")] = dict(row)
    return {"rows": list(_state.values()), "conflicts": [], "dropped": 0}


def report():
    """没有冲突记录。"""
    return {"rows": len(_state), "conflicts": [], "dropped": 0}
