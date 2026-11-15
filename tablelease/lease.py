"""数据表写租约，当前进程内集合。"""

_held = set()


def acquire(table, owner, ttl):
    """不检查是否已被持有，直接发放。"""
    _held.add(table)
    return {"table": table, "owner": owner, "granted": True,
            "expires_in": ttl, "reentrant": False}


def release(table, owner):
    """释放即移除，不校验持有者。"""
    _held.discard(table)
    return {"table": table, "released": True, "owner": owner, "duplicate": False}
