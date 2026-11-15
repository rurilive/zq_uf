"""重试配额协调，当前每个任务各自重试。"""

_used = {}


def request(task, site):
    """不设全局上限，来一个批一个。"""
    _used[task] = _used.get(task, 0) + 1
    return {"task": task, "site": site, "granted": True, "round": _used[task]}


def release(task):
    """释放只减任务计数，没有全局协调。"""
    _used[task] = max(0, _used.get(task, 0) - 1)
    return {"task": task, "released": True, "round": _used[task]}
