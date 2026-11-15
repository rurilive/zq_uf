"""验证码工单，当前单队列顺序处理。"""

_queue = []


def submit(account, task):
    """按到达顺序入队，优先级不生效。"""
    _queue.append({"account": account, "task": dict(task)})
    return {"ticket": len(_queue), "account": account,
            "priority": task.get("priority", 0), "queued": len(_queue)}


def resolve(ticket, code):
    """回写即结束，不确认后续。"""
    return {"ticket": ticket, "resolved": True, "resume": False}
