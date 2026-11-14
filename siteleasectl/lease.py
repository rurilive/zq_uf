"""站点并发租约，当前全局一个上限。"""

LIMIT = 3
_used = 0
_seq = 0


def lease(site, priority=5):
    """所有站点共用一个计数，没有过期。"""
    global _used, _seq
    if _used >= LIMIT:
        raise RuntimeError("global concurrency limit")
    _used += 1
    _seq += 1
    return {"id": "lease-%d" % _seq, "site": site, "priority": priority}


def release(lease_id):
    global _used
    _used = max(0, _used - 1)
    return True


def usage():
    return {"used": _used, "limit": LIMIT}
