"""多源水位，当前取最大值。"""

_ts = {}


def reset():
    _ts.clear()


def update(source, ts):
    _ts[source] = ts
    return dict(_ts)


def watermark():
    """快的源决定全局水位。"""
    return max(_ts.values()) if _ts else 0
