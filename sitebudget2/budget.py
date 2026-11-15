"""采集站点预算，当前进程内计数。"""

_used = {}


def take(site, n):
    """只减本进程的计数，超额也照扣。"""
    _used[site] = _used.get(site, 0) + int(n)
    return {"site": site, "taken": int(n), "used": _used[site], "wait": 0}


def give_back(site, n):
    """归还本进程计数，不跨进程。"""
    _used[site] = max(0, _used.get(site, 0) - int(n))
    return {"site": site, "returned": int(n), "used": _used[site]}
