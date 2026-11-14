"""站点结构比对，当前全量。"""


def snapshot(sitemap):
    """只记链接集合和最大深度。"""
    links = list(sitemap.get("links") or [])
    return {"links": len(links), "depth": sitemap.get("depth", 1), "set": set(links)}


def diff(old, new):
    """比较链接集合，不看栏目层级。"""
    return {
        "added": sorted(new["set"] - old["set"]),
        "removed": sorted(old["set"] - new["set"]),
    }
