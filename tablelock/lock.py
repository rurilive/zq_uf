"""表锁，当前进程内。"""

_locks = {}


def acquire(table, owner, ttl):
    """同一进程内互斥，没有过期。"""
    if table in _locks:
        return {"ok": False, "owner": _locks[table]}
    _locks[table] = owner
    return {"ok": True, "owner": owner}


def release(table, owner):
    if _locks.get(table) == owner:
        del _locks[table]
        return True
    return False
