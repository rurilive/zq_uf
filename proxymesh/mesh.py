"""采集多机房选路，当前随机选。"""
import random

_pools = {}


def pick(site):
    """从出口池里随机挑一个，不看健康分。"""
    proxies = _pools.get(site) or ["proxy-a", "proxy-b"]
    return {"site": site, "proxy": random.choice(proxies), "health": None, "reason": "random"}


def report(proxy, ok, rtt):
    """记录抓取结果，不影响后续选路。"""
    return {"proxy": proxy, "ok": ok, "rtt": rtt, "health": None, "switched": False}
