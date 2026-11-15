"""采集并发爬升，当前所有站点固定并发。"""

_LIMIT = 4
_running = {}


def acquire(site):
    """按固定上限发放并发额度。"""
    used = _running.get(site, 0)
    if used >= _LIMIT:
        return {"site": site, "ok": False, "reason": "full", "concurrency": used}
    _running[site] = used + 1
    return {"site": site, "ok": True, "concurrency": used + 1}


def report(site, ok, rtt):
    """记录一次请求结果，不调整并发。"""
    return {"site": site, "ok": ok, "rtt": rtt, "concurrency": _running.get(site, 0)}
