"""告警登记，当前逐条追加，不做合并。"""

_ALERTS = []


def reset():
    """清空已登记的告警。"""
    _ALERTS.clear()


def emit(alerts):
    """按到达顺序登记告警，每条各存一份。"""
    if not isinstance(alerts, list):
        raise ValueError("alerts must be a list")
    accepted = 0
    for alert in alerts:
        if not isinstance(alert, dict):
            raise ValueError("alert must be a dict")
        for key in ("reason", "host"):
            if not alert.get(key):
                raise ValueError("alert needs reason and host")
        _ALERTS.append({
            "reason": alert["reason"],
            "host": alert["host"],
            "site": alert.get("site", ""),
            "time": alert.get("time", ""),
            "status": alert.get("status", "firing"),
        })
        accepted += 1
    return {"accepted": accepted, "total": len(_ALERTS)}


def report():
    """按原因统计条数，主机数直接取条目数。"""
    counts = {}
    for alert in _ALERTS:
        counts[alert["reason"]] = counts.get(alert["reason"], 0) + 1
    return {
        "total": len(_ALERTS),
        "hosts": len(_ALERTS),
        "by_reason": counts,
        "entries": [dict(alert) for alert in _ALERTS],
    }
