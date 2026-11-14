"""采集出口熔断，当前一次失败就停用全部出口。"""

PROXIES = ["10.0.0.1:8080", "10.0.0.2:8080", "10.0.0.3:8080"]
_disabled = False


def acquire(site):
    """所有站点共用一个开关。"""
    if _disabled:
        raise RuntimeError("all proxies disabled")
    return PROXIES[0]


def report(site, proxy, ok):
    """任意出口失败都会关掉整个池子。"""
    global _disabled
    if not ok:
        _disabled = True
    return _disabled
