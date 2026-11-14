"""站点许可，当前进程内集合。"""

_blocked = {}


def block(host, reason, ttl):
    """只记内存，不看到期。"""
    _blocked[host] = {"reason": reason, "ttl": ttl}
    return dict(_blocked[host])


def allowed(host):
    return host not in _blocked
