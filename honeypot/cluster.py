"""蜜罐访问聚类，当前按来源地址分桶。"""

_GROUPS = []


def reset():
    """清空上一轮分组结果。"""
    _GROUPS.clear()


def cluster(events):
    """按来源地址分组，每个地址一组。"""
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    buckets = {}
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("event must be a dict")
        for key in ("ip", "path", "at"):
            if event.get(key) is None:
                raise ValueError("event needs ip, path and at")
        buckets.setdefault(event["ip"], []).append(event)
    groups = []
    for ip in sorted(buckets):
        rows = buckets[ip]
        groups.append({
            "ip": ip,
            "count": len(rows),
            "first": min(row["at"] for row in rows),
            "last": max(row["at"] for row in rows),
            "paths": len({row["path"] for row in rows}),
        })
    _GROUPS.clear()
    _GROUPS.extend(groups)
    return {"groups": [dict(group) for group in groups], "sources": len(groups), "events": len(events)}


def report():
    """按访问条数从多到少给出分组。"""
    ordered = sorted(_GROUPS, key=lambda group: group["count"], reverse=True)
    return {
        "sources": len(_GROUPS),
        "events": sum(group["count"] for group in _GROUPS),
        "top": [dict(group) for group in ordered],
    }
