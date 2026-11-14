"""采集任务租约，当前直接覆盖持有者。"""

_leases = {}


def claim(task_id, owner, ttl):
    """后到的直接覆盖，不看过期和栅栏。"""
    _leases[task_id] = owner
    return {"task_id": task_id, "owner": owner}


def renew(task_id, owner, ttl):
    return _leases.get(task_id) == owner


def release(task_id, owner):
    if _leases.get(task_id) == owner:
        _leases.pop(task_id, None)
    return True
