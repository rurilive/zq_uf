"""任务时间预算，当前固定超时。"""

_BUDGET = 3600


def start(job):
    """所有任务同一预算。"""
    return {"job": job.get("name", ""), "budget": _BUDGET,
            "degraded": False, "reason": ""}


def checkpoint(job, progress):
    """进度不保存。"""
    return {"job": job, "saved": False, "remaining": _BUDGET, "degraded": False}
