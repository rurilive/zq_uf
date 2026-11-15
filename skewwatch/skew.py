"""数据倾斜检测，当前只看总耗时。"""

_tasks = {}


def observe(task):
    """记录总耗时，不区分分区。"""
    name = task.get("name", "")
    _tasks[name] = float(task.get("elapsed", 0))
    return {"task": name, "elapsed": _tasks[name], "skewed": False}


def report(task):
    """只返回总耗时。"""
    elapsed = _tasks.get(task, 0.0)
    return {"task": task, "elapsed": elapsed, "hot": [], "ratio": 1.0, "advice": ""}
