"""站点健康评分，当前只记事件数。"""

_events = {}


def report(site, event):
    """累计事件数，评分固定。"""
    _events.setdefault(site, []).append(dict(event))
    return {"site": site, "events": len(_events[site]), "score": 1.0}


def score(site):
    """固定满分，没有分项。"""
    return {"site": site, "score": 1.0, "parts": {}, "trend": "flat"}
