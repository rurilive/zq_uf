"""任务时间预算，当前所有人同一超时。"""

TIMEOUT = 60


def start(task):
    """不区分任务类型和优先级。"""
    return {"task": task, "budget": TIMEOUT}


def checkpoint(task, progress):
    """进度不保存。"""
    return {"task": task, "saved": False}
