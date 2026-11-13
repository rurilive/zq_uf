"""先进先出的任务队列。"""


class TaskQueue:
    def __init__(self):
        self.pending = []
        self.done = []

    def push(self, task):
        self.pending.append(task)

    def lease(self, worker):
        if not self.pending:
            return None
        return self.pending.pop(0)

    def complete(self, task):
        self.done.append(task)
