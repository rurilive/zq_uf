"""采集任务租约，当前谁领都给。"""

_held = {}


def acquire(task, owner):
    """不检查持有者，直接覆盖。"""
    _held[task] = owner
    return {"task": task, "owner": owner, "granted": True, "expires_in": 0}


def renew(task, owner):
    """续约永远成功。"""
    return {"task": task, "owner": owner, "renewed": True, "expires_in": 0}
