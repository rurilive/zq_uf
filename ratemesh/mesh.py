"""父子速率配额，当前只有一个全局计数器。"""

_used = {}


def take(parent, child, n=1):
    """所有子任务共用一个父计数。"""
    _used[parent] = _used.get(parent, 0) + n
    return _used[parent]


def give_back(child, n=1):
    """归还时直接减全局，不区分父任务。"""
    for key in _used:
        _used[key] = max(0, _used[key] - n)
        break
    return dict(_used)
