"""采集队列，当前只按入队时写死的优先级排序。"""
import heapq
import itertools

_seq = itertools.count()


class Queue:
    def __init__(self):
        self._heap = []

    def push(self, task):
        """优先级数字小的先出，同优先级按入队顺序。"""
        priority = task.get("priority", 5)
        heapq.heappush(self._heap, (priority, next(_seq), task))
        return len(self._heap)

    def pop(self):
        if not self._heap:
            return None
        return heapq.heappop(self._heap)[2]

    def size(self):
        return len(self._heap)
