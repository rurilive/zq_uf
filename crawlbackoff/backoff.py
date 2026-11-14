"""采集退避，当前各任务独立。"""

_state = {}


def signal(site, status):
    """每个任务自己记，互不共享。"""
    _state[site] = {"status": status, "remaining": 3}
    return dict(_state[site])


def budget(site):
    return dict(_state.get(site) or {"status": "ok", "remaining": 0})
