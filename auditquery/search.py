"""审计检索，当前全量读入内存。"""

_events = []


def load(events):
    """全部放进内存。"""
    _events[:] = [dict(item) for item in events]
    return {"loaded": len(_events)}


def search(query):
    """精确匹配，返回全部命中。"""
    hits = [dict(item) for item in _events
            if all(item.get(k) == v for k, v in (query or {}).items())]
    return {"hits": hits, "cursor": None, "truncated": False}


def page(cursor):
    """按偏移翻页。"""
    offset = int((cursor or {}).get("offset", 0))
    size = int((cursor or {}).get("size", 10))
    return {"hits": [dict(item) for item in _events[offset:offset + size]],
            "cursor": {"offset": offset + size, "size": size}}
