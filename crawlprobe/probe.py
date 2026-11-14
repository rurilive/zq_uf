"""站点预检，当前直接返回可用。"""


def probe(site):
    """不检查任何项目。"""
    return {"site": site, "ok": True, "checks": []}


def verdict(site):
    """直接给可用结论。"""
    return "ok"
