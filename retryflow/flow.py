"""采集任务的重试编排，重试和熔断目前各管各的。"""

_MAX_ATTEMPT = 3
_attempts = {}


def retry(task, site):
    """失败后按任务计数重试，不看站点是否已熔断。"""
    used = _attempts.get(task, 0)
    if used >= _MAX_ATTEMPT:
        return {"task": task, "site": site, "action": "give_up", "attempts": used}
    _attempts[task] = used + 1
    return {"task": task, "site": site, "action": "retry", "attempts": used + 1}


def outcome(task):
    """返回单个任务已经用掉的重试次数。"""
    return {"task": task, "attempts": _attempts.get(task, 0)}
