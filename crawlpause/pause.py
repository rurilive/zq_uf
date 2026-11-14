"""任务暂停恢复，当前只置标志。"""

_paused = set()


def pause(task):
    """直接置暂停，不等在跑批次。"""
    _paused.add(task)
    return {"task": task, "paused": True}


def resume(task):
    _paused.discard(task)
    return {"task": task, "paused": False}


def is_paused(task):
    return task in _paused
