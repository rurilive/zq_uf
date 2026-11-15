"""审计检索，当前全量读入内存。"""

_events = []


def load(events):
    """把全部审计一次放进内存。"""
    _events[:] = list(events)
    return {"loaded": len(_events)}


def search(query):
    """精确匹配后返回全部命中，不分页。"""
    hits = []
    for event in _events:
        if all(event.get(key) == value for key, value in (query or {}).items()):
            hits.append(dict(event))
    return {"hits": hits, "cursor": None, "truncated": False}


def page(cursor):
    """游标就是偏移量，翻页时数据变化会重复或漏。"""
    offset = int((cursor or {}).get("offset", 0))
    size = int((cursor or {}).get("size", 10))
    chunk = _events[offset:offset + size]
    return {"hits": chunk, "cursor": {"offset": offset + size, "size": size}}
