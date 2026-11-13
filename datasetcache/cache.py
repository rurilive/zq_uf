"""数据集查询缓存，当前只按固定TTL过期。"""
import time

TTL = 3600
_cache = {}
_misses = 0
_invalidated = 0


def clear():
    global _misses, _invalidated
    _cache.clear()
    _misses = 0
    _invalidated = 0


def put(key, value, now=None):
    now = time.time() if now is None else now
    _cache[key] = {"value": value, "expires": now + TTL}
    return len(_cache)


def get(key, now=None):
    global _misses
    now = time.time() if now is None else now
    entry = _cache.get(key)
    if not entry or entry["expires"] <= now:
        _misses += 1
        return None
    return entry["value"]


def invalidate(dataset=None):
    """不管数据集，整表清空。"""
    global _invalidated
    count = len(_cache)
    _cache.clear()
    _invalidated += count
    return count


def stats():
    return {"size": len(_cache), "misses": _misses, "invalidated": _invalidated}
