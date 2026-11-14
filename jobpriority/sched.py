"""任务调度，当前先进先出。"""

_queue = []


def submit(job):
    _queue.append(dict(job))
    return len(_queue)


def next_job():
    return _queue.pop(0) if _queue else None
