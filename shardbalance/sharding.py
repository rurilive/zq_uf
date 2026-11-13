"""采集任务按站点取模分配到执行机。"""

WORKERS = ["m1", "m2", "m3"]
SITES = ["shop", "news", "blog", "forum", "video", "mall", "wiki", "map"]


def _bucket(site):
    return sum(site.encode("utf-8")) % 100000


def assign(site, workers=None):
    """按取模结果选一台机器，不记录在跑状态。"""
    pool = list(WORKERS if workers is None else workers)
    if not pool:
        raise ValueError("workers 不能为空")
    return pool[_bucket(site) % len(pool)]


def rebalance(workers=None):
    """整表重算，所有站点一起换，没有迁移清单。"""
    return {site: assign(site, workers) for site in SITES}
