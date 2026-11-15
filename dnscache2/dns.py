"""采集DNS解析，当前每次直接返回固定地址。"""

_ADDR = {"shop": "10.0.0.1"}


def resolve(host):
    """不缓存也不看健康度。"""
    return {"host": host, "ip": _ADDR.get(host, "127.0.0.1"), "ttl": 0, "cached": False}


def report(host, ip, ok, rtt):
    """记录结果但不影响后续解析。"""
    return {"host": host, "ip": ip, "ok": ok, "health": 1.0, "refreshed": False}
