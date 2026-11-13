"""只追加的增量聚合。"""


class Aggregator:
    def __init__(self):
        self.totals = {}

    def apply(self, batch):
        for row in batch:
            key = row.get("key")
            self.totals[key] = self.totals.get(key, 0) + row.get("value", 0)
        return self.totals

    def query(self, key):
        return self.totals.get(key, 0)
