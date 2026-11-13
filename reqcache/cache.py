"""请求缓存，当前用URL字符串当键，永不过期。"""

_cache = {}
_hits = 0
_misses = 0


def clear():
    global _hits, _misses
    _cache.clear()
    _hits = 0
    _misses = 0


def get(key):
    global _hits, _misses
    if key in _cache:
        _hits += 1
        return _cache[key]
    _misses += 1
    return None


def put(key, resp):
    """键原样使用，容量不设上限。"""
    _cache[key] = resp
    return len(_cache)


def stats():
    return {"hits": _hits, "misses": _misses, "size": len(_cache)}
