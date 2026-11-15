"""站点隔离区，当前只是跳过。"""

_isolated = {}


def quarantine(host, reason):
    """记下原因，但没有样本和到期时间。"""
    _isolated[host] = reason
    return {"host": host, "reason": reason, "samples": [], "until": None}


def probe(host):
    """探测一律返回可用，不读隔离状态。"""
    return {"host": host, "ok": True, "verdict": "open", "samples": []}
