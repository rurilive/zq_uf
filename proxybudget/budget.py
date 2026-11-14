"""代理流量配额，当前一个全局计数。"""

_spent = 0
LIMIT = 1000


def consume(site, proxy, amount):
    """站点和出口参数只记录，不影响计数。"""
    global _spent
    if _spent + amount > LIMIT:
        raise RuntimeError("global quota exceeded")
    _spent += amount
    return _spent


def remaining(site=None):
    return LIMIT - _spent
