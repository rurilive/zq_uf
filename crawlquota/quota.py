"""采集进程资源配额，当前是固定上限的事前检查。"""

CAPS = {"thread": 32, "fd": 256, "memory_mb": 2048}
_used = {key: 0 for key in CAPS}


def acquire(task, kind, amount=1):
    """直接扣减，超上限抛错，不排队。"""
    if kind not in CAPS:
        raise KeyError(kind)
    if _used[kind] + amount > CAPS[kind]:
        raise RuntimeError(f"{kind} quota exceeded")
    _used[kind] += amount
    return _used[kind]


def release(task, kind, amount=1):
    _used[kind] = max(0, _used[kind] - amount)
    return _used[kind]


def usage():
    return dict(_used)
