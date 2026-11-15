"""质量趋势，当前单日阈值。"""

_history = {}


def check(dataset, metric):
    """固定阈值，超过就告警。"""
    value = float(metric.get("value", 0))
    _history.setdefault(dataset, []).append(value)
    return {"dataset": dataset, "value": value, "alert": value > 0.05, "baseline": None}


def trend(dataset):
    """只回显历史值。"""
    return {"dataset": dataset, "history": list(_history.get(dataset, [])), "direction": None}
