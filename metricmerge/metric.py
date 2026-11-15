"""指标合并，当前即时累加且不重算。"""

_windows = {}


def add(metric):
    """按窗口直接累加，迟到数据也加在当前窗口。"""
    window = metric.get("window", "")
    bucket = _windows.setdefault(window, {"count": 0, "sum": 0.0, "values": []})
    bucket["count"] += 1
    bucket["sum"] += float(metric.get("value", 0))
    bucket["values"].append(float(metric.get("value", 0)))
    return {"window": window, "count": bucket["count"], "sum": bucket["sum"]}


def recompute(window):
    """没有历史版本，重算只是把当前值再返回一次。"""
    bucket = _windows.get(window, {"count": 0, "sum": 0.0, "values": []})
    return {"window": window, "recomputed": False, "count": bucket["count"], "sketch": None}
