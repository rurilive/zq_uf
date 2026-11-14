"""恢复演练，当前只回显成功。"""

_history = []


def drill(snapshot):
    """不恢复也不校验。"""
    _history.append(snapshot)
    return {"snapshot": snapshot, "ok": True, "target": "/tmp"}


def report():
    return {"drills": len(_history)}
