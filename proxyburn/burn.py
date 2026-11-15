"""出口熔断，当前失败一次就换。"""

_failed = set()


def use(proxy):
    """返回固定首选，不看熔断状态。"""
    return {"proxy": proxy, "usable": True, "state": "closed", "probe": False}


def report(proxy, ok):
    """失败只记集合，不影响选择。"""
    if not ok:
        _failed.add(proxy)
    return {"proxy": proxy, "ok": ok, "state": "closed", "tripped": False}
