"""指标上卷，当前固定窗口累加。"""

_windows = {}


def add(metric):
    """按给定窗口累加。"""
    key = (metric.get("dim", ""), metric.get("window", ""))
    bucket = _windows.setdefault(key, {"count": 0, "sum": 0.0, "values": []})
    bucket["count"] += 1
    bucket["sum"] += float(metric.get("value", 0))
    bucket["values"].append(float(metric.get("value", 0)))
    return {"dim": key[0], "window": key[1], "count": bucket["count"]}


def query(dim, window):
    """分位数取样本平均。"""
    bucket = _windows.get((dim, window), {"count": 0, "sum": 0.0, "values": []})
    values = bucket["values"]
    return {"dim": dim, "window": window, "count": bucket["count"],
            "sum": bucket["sum"], "p50": sum(values) / len(values) if values else 0.0}
