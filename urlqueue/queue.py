"""链接队列，当前先进先出。"""

_queue = []


def push(url, priority=5):
    """优先级只保存，不参与排序。"""
    _queue.append({"url": url, "priority": priority})
    return len(_queue)


def pop():
    return _queue.pop(0) if _queue else None
