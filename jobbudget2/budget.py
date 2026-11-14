"""任务预算，当前固定超时。"""

TIMEOUT = 60


def start(job):
    """所有任务同一预算。"""
    return {"job": job, "budget": TIMEOUT}


def checkpoint(job, progress):
    """不保存进度。"""
    return {"job": job, "saved": False}
