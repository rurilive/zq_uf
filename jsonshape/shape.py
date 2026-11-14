"""结构巡检，当前所有站点共用一个基线。"""

_baseline = None
_alerts = []


def reset():
    global _baseline
    _baseline = None
    _alerts.clear()


def observe(site, payload):
    """只看顶层字段名。"""
    global _baseline
    paths = sorted(payload.keys())
    if _baseline is None:
        _baseline = paths
        return {"site": site, "alert": False}
    alert = paths != _baseline
    if alert:
        _alerts.append({"site": site, "paths": paths})
    return {"site": site, "alert": alert}


def report(site=None):
    return {"alerts": list(_alerts) if site is None else [a for a in _alerts if a["site"] == site]}
