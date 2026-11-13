"""robots规则读取，当前每次请求都重新拉取。"""

_fetcher = None


def set_fetcher(func):
    global _fetcher
    _fetcher = func


def _load(host):
    if _fetcher is None:
        raise RuntimeError("fetcher 未设置")
    return _fetcher(host)


def allowed(url):
    """每次都拉规则，也没有过期概念。"""
    host = url.split("//", 1)[-1].split("/", 1)[0]
    rules = _load(host)
    path = "/" + url.split("//", 1)[-1].split("/", 1)[-1] if "//" in url else url
    for prefix in rules.get("disallow", []):
        if path.startswith(prefix):
            return False
    return True


def refresh(host):
    """显式刷新也是直接再拉一次。"""
    return _load(host)
