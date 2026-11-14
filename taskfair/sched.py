"""多队列调度，当前全局先进先出。"""

_queue = []


def reset():
    _queue.clear()


def submit(queue, task):
    """队列名只保存，不影响顺序。"""
    _queue.append((queue, dict(task)))
    return len(_queue)


def next_task():
    if not _queue:
        return None
    return _queue.pop(0)[1]
