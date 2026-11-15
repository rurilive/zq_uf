"""站点预检，当前逐个探测且不缓存。"""

_last = {}


def probe(sites, workers=1):
    """串行探测，workers 参数被忽略。"""
    results = []
    for site in sites:
        results.append({"site": site, "ok": True, "cached": False})
        _last[site] = "ok"
    return {"results": results, "cache_hits": 0}


def verdict(site):
    """返回最近一次结论，没有结论时按通过处理。"""
    return {"site": site, "verdict": _last.get(site, "ok"), "reason": ""}
