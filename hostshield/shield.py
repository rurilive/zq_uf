"""单站点限速，当前全局固定间隔。"""

INTERVAL = 1.0
_last = [0.0]


def acquire(site):
    """所有站点共用一个间隔。"""
    _last[0] += INTERVAL
    return {"site": site, "at": round(_last[0], 3)}


def release(site, rtt, status):
    """RTT和状态码都不参与调整。"""
    return {"rate": 1.0 / INTERVAL, "concurrency": 1}
