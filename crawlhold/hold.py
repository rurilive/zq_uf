"""采集任务挂起，当前收到信号就直接退出。"""


def hold(task):
    """立即退出，不等待在跑请求。"""
    return {"task": task, "phase": "exited", "released": False}


def release(task):
    """恢复时没有检查点，只能重新入队。"""
    return {"task": task, "phase": "idle", "resumed": False}
