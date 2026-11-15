"""质量趋势，当前只看单日固定阈值。"""

_history = {}


def check(dataset, metric):
    """单日阈值写死，超过就告警。"""
    value = float(metric.get("value", 0))
    alert = value > 0.05
    _history.setdefault(dataset, []).append(value)
    return {"dataset": dataset, "value": value, "alert": alert, "baseline": None}


def trend(dataset):
    """只返回历史值，不算趋势。"""
    return {"dataset": dataset, "history": list(_history.get(dataset, [])), "direction": None}
