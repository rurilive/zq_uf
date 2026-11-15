"""robots 规则，当前一律放行。"""


def allowed(url):
    """不拉取也不解析。"""
    return {"url": url, "allowed": True, "rule": None, "cached": False}


def refresh(host):
    """没有缓存可刷新。"""
    return {"host": host, "refreshed": False, "version": None}
