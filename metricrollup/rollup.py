"""指标上卷，当前按自然小时累加。"""

_buckets = {}


def reset():
    _buckets.clear()


def add(metric):
    """按时间戳整除3600落到自然小时。"""
    hour = int(metric["ts"]) // 3600
    key = (metric["dim"], hour)
    bucket = _buckets.setdefault(key, {"count": 0, "sum": 0})
    bucket["count"] += 1
    bucket["sum"] += metric["value"]
    return key


def query(dim, window=None):
    """只返回计数和求和，没有分位数。"""
    rows = [b for (d, h), b in sorted(_buckets.items()) if d == dim]
    return {
        "count": sum(b["count"] for b in rows),
        "sum": sum(b["sum"] for b in rows),
        "buckets": len(rows),
    }
