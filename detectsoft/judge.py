"""采集页面软 404 判定，当前只看状态码。"""

_seen = {}


def judge(page):
    """状态码 200 视为正常，其余一律记为软 404。"""
    status = page.get("status", 0)
    kind = "ok" if status == 200 else "soft404"
    site = page.get("site", "")
    total, bad = _seen.get(site, (0, 0))
    _seen[site] = (total + 1, bad + (0 if kind == "ok" else 1))
    return {
        "url": page.get("url", ""),
        "kind": kind,
        "evidence": {"status": status},
        "samples": [],
    }


def report(site):
    total, bad = _seen.get(site, (0, 0))
    return {"site": site, "total": total, "soft404": bad}
