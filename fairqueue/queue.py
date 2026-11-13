"""采集任务队列，当前是全局先进先出。"""


class TaskQueue:
    def __init__(self):
        self._items = []

    def push(self, task):
        """入队，重试任务也排到队尾。"""
        self._items.append(task)

    def next(self, worker=None):
        if not self._items:
            return None
        return self._items.pop(0)

    def size(self):
        return len(self._items)
