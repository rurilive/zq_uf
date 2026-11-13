"""采集任务配额，当前所有站点共用一个全局计数。"""

LIMIT = 1000
_spent = 0


def reset():
    global _spent
    _spent = 0


def consume(site, n=1):
    """扣减额度，超额直接抛错。"""
    global _spent
    if _spent + n > LIMIT:
        raise RuntimeError("quota exceeded")
    _spent += n
    return _spent


def remaining(site=None):
    """剩余额度，站点参数当前被忽略。"""
    return LIMIT - _spent
