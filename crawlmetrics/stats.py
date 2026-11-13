"""采集计数的最小实现。"""


class Stats:
    def __init__(self):
        self.counts = {}

    def inc(self, name, value=1):
        self.counts[name] = self.counts.get(name, 0) + value

    def snapshot(self):
        return dict(self.counts)
