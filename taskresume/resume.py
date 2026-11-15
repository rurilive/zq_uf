"""采集任务断点恢复，当前从头重跑。"""

_points = {}


def save(task, state):
    """检查点只放内存，不记录入库位点。"""
    _points[task] = dict(state)
    return {"task": task, "saved": True, "durable": False, "checksum": None}


def resume(task):
    """恢复一律从零开始。"""
    return {"task": task, "start": 0, "skipped": [], "rolled_back": False}
