"""站点调度，当前所有站点等权轮转。"""

SITES = ["shop", "news", "blog"]
_index = 0
_results = []


def reset():
    global _index
    _index = 0
    _results.clear()


def next_site():
    """按固定顺序轮转，权重写死在代码里。"""
    global _index
    site = SITES[_index % len(SITES)]
    _index += 1
    return site


def feedback(site, result):
    """记录结果，但不影响下一次选择。"""
    _results.append({"site": site, "ok": bool(result)})
    return len(_results)
