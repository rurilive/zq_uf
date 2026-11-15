"""采集链接重写，当前原样返回。"""


def rewrite(url, site):
    """不区分分页和追踪参数，直接返回原链接。"""
    return {"url": url, "site": site, "hit": [], "before": url, "after": url}


def rules(site):
    """没有可配置规则。"""
    return {"site": site, "rules": [], "conflicts": []}
