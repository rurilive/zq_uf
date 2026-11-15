"""表操作审计，当前逐条记录。"""

_events = []


def record(event):
    """每条操作单独入表，不去重也不聚合。"""
    _events.append(dict(event))
    return {"recorded": True, "total": len(_events), "aggregated": False}


def query(filters):
    """全量扫描后按字段精确匹配。"""
    hits = []
    for event in _events:
        if all(event.get(key) == value for key, value in (filters or {}).items()):
            hits.append(dict(event))
    return {"hits": hits, "cursor": None, "aggregated": False}
