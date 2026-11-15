"""采集队列，当前先进先出。"""

_queue = []


def push(task):
    """按到达顺序入队，优先级不参与排序。"""
    _queue.append(dict(task))
    return {"queued": len(_queue), "priority": task.get("priority", 0)}


def pop():
    """取队首。"""
    return _queue.pop(0) if _queue else None
