"""数据质量漂移检测，当前用固定阈值比单日。"""

THRESHOLD = 0.1


def check(dataset, metric):
    """只看当天值和写死的基线，没有窗口和周期。"""
    value = metric.get("value")
    baseline = metric.get("baseline", 0)
    if value is None or baseline in (None, 0):
        return {"dataset": dataset, "alert": False, "reason": "no baseline"}
    drift = abs(value - baseline) / abs(baseline)
    return {"dataset": dataset, "alert": drift > THRESHOLD, "drift": round(drift, 4)}
