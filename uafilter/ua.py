"""请求头适配，当前一套头走天下。"""

_UA = "Mozilla/5.0 crawler"
_stats = {}


def headers(site):
    """站点参数被忽略。"""
    return {"User-Agent": _UA, "Accept-Language": "zh-CN"}


def report(site, status):
    _stats[status] = _stats.get(status, 0) + 1
    return dict(_stats)
