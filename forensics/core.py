"""把各来源的时间原样排成一条时间线。"""

_LAST = []


def timeline(evidence):
    """按时间字符串排序，时区不参与换算。"""
    if not isinstance(evidence, list) or not evidence:
        raise ValueError("evidence 不能为空")
    events = []
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise ValueError("证据项必须是字典")
        raw = item.get("time")
        if not isinstance(raw, str) or not raw:
            raise ValueError("证据项缺少时间")
        events.append({
            "id": item.get("id", "e%d" % index),
            "time": raw,
            "source": item.get("source", "unknown"),
            "subject": item.get("subject", ""),
            "action": item.get("action", ""),
        })
    events.sort(key=lambda event: event["time"])
    _LAST[:] = events
    return {
        "events": [dict(event) for event in events],
        "count": len(events),
        "sources": sorted({event["source"] for event in events}),
        "merged": 0,
        "conflicts": [],
        "skew": 0,
    }


def report():
    """按来源给出事件条数。"""
    sources = {}
    for event in _LAST:
        sources[event["source"]] = sources.get(event["source"], 0) + 1
    return {
        "events": len(_LAST),
        "sources": sources,
        "merged": 0,
        "conflicts": 0,
        "skew": 0,
    }


def by_source(source):
    """按来源筛出事件，不做归一。"""
    if not isinstance(source, str) or not source:
        raise ValueError("source 不能为空")
    return [dict(event) for event in _LAST if event["source"] == source]
