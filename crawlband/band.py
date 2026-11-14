"""采集带宽，当前全局固定速率。"""

RATE = 1024
_used = 0


def take(task, amount):
    """所有任务共用一个速率，没有借还。"""
    global _used
    if _used + amount > RATE:
        raise RuntimeError("bandwidth exceeded")
    _used += amount
    return amount


def give_back(task, amount):
    global _used
    _used = max(0, _used - amount)
    return _used
