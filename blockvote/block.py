"""封禁信号，当前一个403就停站点。"""

_blocked = set()


def observe(site, signal):
    """只看403，其他信号忽略。"""
    if signal == "403":
        _blocked.add(site)
    return site in _blocked


def allow(site):
    return site not in _blocked
