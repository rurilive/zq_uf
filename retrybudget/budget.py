"""采集重试，当前每层各自重试三次。"""

MAX_ATTEMPTS = 3


def with_budget(task, budget):
    """预算只挂在任务上，没人读。"""
    task = dict(task)
    task["budget"] = budget
    return task


def attempt(task):
    """不看预算，固定重试三次。"""
    used = int(task.get("attempts", 0)) + 1
    task["attempts"] = used
    return {"attempt": used, "retry": used < MAX_ATTEMPTS}
