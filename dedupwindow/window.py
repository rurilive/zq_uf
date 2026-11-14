"""流式去重，当前集合无限增长。"""

_seen = set()


def reset():
    _seen.clear()


def seen(key, ts):
    """不看时间，永久保留。"""
    if key in _seen:
        return True
    _seen.add(key)
    return False


def advance(watermark):
    """水位只返回大小，不清理。"""
    return len(_seen)
