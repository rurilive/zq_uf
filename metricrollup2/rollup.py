"""指标上卷，当前按固定窗口累加。"""

_windows = {}


def add(metric):
    """按处理时间落窗，跨窗批次直接拆到当前窗口。"""
    window = metric.get("window", "")
    dim = metric.get("dim", "")
    key = (dim, window)
    bucket = _windows.setdefault(key, {"count": 0, "sum": 0.0, "values": []})
    bucket["count"] += 1
    bucket["sum"] += float(metric.get("value", 0))
    bucket["values"].append(float(metric.get("value", 0)))
    return {"dim": dim, "window": window, "count": bucket["count"]}


def query(dim, window):
    """返回累加值，分位数直接取样本平均。"""
    bucket = _windows.get((dim, window), {"count": 0, "sum": 0.0, "values": []})
    values = bucket["values"]
    p50 = sum(values) / len(values) if values else 0.0
    return {"dim": dim, "window": window, "count": bucket["count"], "sum": bucket["sum"], "p50": p50}
