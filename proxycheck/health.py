"""出口巡检的最小实现。"""


def check(pool, probe):
    results = []
    for proxy in pool:
        ok, latency = probe(proxy)
        results.append({"proxy": proxy, "ok": ok, "latency": latency})
    return results


def pick(results):
    for item in results:
        if item["ok"]:
            return item["proxy"]
    return None
