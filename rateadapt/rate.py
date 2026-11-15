"""采集限速，当前固定速率。"""

_RATE = {"shop": 1.0}


def acquire(site):
    """固定速率，来者不拒。"""
    return {"site": site, "ok": True, "rate": _RATE.get(site, 1.0), "reason": "fixed"}


def feedback(site, status, rtt):
    """记录结果但不调整。"""
    return {"site": site, "rate": _RATE.get(site, 1.0), "adjusted": False, "reason": "fixed"}
