"""数据任务调度，当前先来先跑。"""


class Queue:
    def __init__(self):
        self._items = []

    def submit(self, task):
        """资源需求只记录不参与排序。"""
        self._items.append(dict(task))
        return len(self._items)

    def next(self):
        if not self._items:
            return None
        return self._items.pop(0)

    def size(self):
        return len(self._items)
