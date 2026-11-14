"""批处理窗口，当前按自然日。"""

_buckets = {}


def assign(event):
    """按时间戳整除一天落窗。"""
    day = int(event["ts"]) // 86400
    _buckets.setdefault(day, []).append(event)
    return {"window": day, "count": len(_buckets[day])}


def recompute(window):
    """重算只返回条数，不撤销旧值。"""
    return {"window": window, "count": len(_buckets.get(window) or [])}
