"""失败重试，当前立即重试三次。"""

_tasks = {}


def schedule(task, site=None):
    """每个任务自己计数，不看站点。"""
    item = _tasks.setdefault(task, {"attempts": 0})
    item["attempts"] += 1
    return {"task": task, "attempts": item["attempts"], "retry": item["attempts"] < 3}


def due(now=None):
    """到期任务按插入顺序返回。"""
    return [task for task, item in _tasks.items() if item["attempts"] < 3]
