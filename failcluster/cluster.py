"""采集失败统计，当前只有总数和最后一条样本。"""

_state = {"total": 0, "last": None}


def reset():
    _state["total"] = 0
    _state["last"] = None


def record(failure):
    """记一条失败，覆盖上一条样本。"""
    _state["total"] += 1
    _state["last"] = failure
    return _state["total"]


def report():
    return {"total": _state["total"], "last": _state["last"]}
