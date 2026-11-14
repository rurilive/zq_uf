"""采集预热，当前直接满速。"""

_current = {}


def warmup(site):
    """一步到最大并发。"""
    _current[site] = 8
    return {"site": site, "concurrency": 8}


def current(site):
    return _current.get(site, 8)
