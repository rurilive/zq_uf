"""迟到指标处理，当前直接累加。"""

_windows = {}


def add(metric):
    """按窗口累加，迟到也加。"""
    window = metric.get("window", "")
    bucket = _windows.setdefault(window, {"count": 0, "sum": 0.0})
    bucket["count"] += 1
    bucket["sum"] += float(metric.get("value", 0))
    return {"window": window, "count": bucket["count"], "sum": bucket["sum"]}


def recompute(window):
    """重算只是回读，不撤销也不重写。"""
    bucket = _windows.get(window, {"count": 0, "sum": 0.0})
    return {"window": window, "recomputed": False, "count": bucket["count"],
            "sum": bucket["sum"], "retracted": None}
