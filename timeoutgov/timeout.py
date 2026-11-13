"""任务超时，当前所有任务共用一个固定上限。"""

LIMIT_SECONDS = 30
_clock = None
_attempts = []


def set_clock(func):
    global _clock
    _clock = func


def run(task, deadline=None):
    """超时直接抛错，进度不保存，重试从头再跑。"""
    limit = LIMIT_SECONDS if deadline is None else deadline
    work = task["work"]
    started = _clock() if _clock else 0
    result = work()
    finished = _clock() if _clock else limit
    if finished - started > limit:
        raise TimeoutError(f"task {task.get('name')} exceeded {limit}s")
    _attempts.append({"name": task.get("name"), "started": started})
    return {"result": result, "attempts": len(_attempts)}
